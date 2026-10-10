"""Route-graph search: find candidate paths, then stitch real flights into itineraries."""
import heapq
from concurrent.futures import ThreadPoolExecutor

from . import api


def parse_places(spec, airports):
    """'DE' -> every airport in Germany, 'CGN,FKB' -> those airports."""
    out = set()
    for part in spec.upper().replace(" ", "").split(","):
        if len(part) == 2:
            out |= {c for c, a in airports.items() if a["country"] == part}
        elif part:
            out.add(part)
    return out


def candidate_paths(origins, dests, max_stops, d_from, d_to, max_candidates, pool, cheapest=None, routes=None):
    """Airport sequences from origins to dests, ranked by a lower-bound price estimate.

    Direct and one-stop paths are always returned in full. Two-stop paths grow
    into the thousands, so only the `max_candidates` cheapest by estimate are kept.
    The estimate comes from one "cheapest fare to everywhere" request per airport.
    """
    cheapest = cheapest or api.cheapest_from
    routes = routes or api.routes

    def lb(airport):
        return cheapest(airport, d_from, d_to)

    list(pool.map(lb, origins))
    paths = {}

    for o in origins:
        for d in dests & routes(o):
            if d != o:
                paths[(o, d)] = lb(o).get(d, float("inf"))

    if max_stops >= 1:
        hubs = set()
        for o in origins:
            hubs |= set(lb(o))
        hubs -= dests
        list(pool.map(lb, hubs))
        for o in origins:
            for h, p1 in lb(o).items():
                if h in dests:
                    continue
                for d, p2 in lb(h).items():
                    if d in dests and d != o:
                        paths[(o, h, d)] = p1 + p2

        if max_stops >= 2:
            # Last hub must fly to a destination. Routes are (almost always) served
            # in both directions, so the destination's own route list gives these.
            pre_dest = set().union(*(routes(d) for d in dests)) - dests
            list(pool.map(lb, pre_dest - hubs))
            for o in origins:
                for h1, p1 in lb(o).items():
                    if h1 in dests or h1 in origins:
                        continue
                    for h2, p2 in lb(h1).items():
                        if h2 in pre_dest and h2 not in (o, h1) and h2 not in origins:
                            for d, p3 in lb(h2).items():
                                if d in dests:
                                    paths[(o, h1, h2, d)] = p1 + p2 + p3

    ranked = sorted(paths.items(), key=lambda kv: kv[1])
    one_stop = [p for p, _ in ranked if len(p) <= 3]
    two_stop = [p for p, _ in ranked if len(p) == 4][:max_candidates]
    return one_stop + two_stop


def stitch(legs, min_layover, max_layover, arrive_by=None, depart_after=None):
    """Combine per-leg flight lists into itineraries whose layovers are realistic.

    `legs` is a list of flight lists, one per leg, in order. All times are UTC.
    """
    if not legs or any(not leg for leg in legs):
        return []
    legs = [sorted(leg, key=lambda f: f.dep) for leg in legs]
    first = [f for f in legs[0] if not depart_after or f.dep >= depart_after]
    results = []

    def extend(chain, idx):
        if idx == len(legs):
            if not arrive_by or chain[-1].arr <= arrive_by:
                results.append(tuple(chain))
            return
        for f in legs[idx]:
            if min_layover <= f.dep - chain[-1].arr <= max_layover:
                extend([*chain, f], idx + 1)

    for f in first:
        extend([f], 1)
    return results


def itineraries_for(path, d_from, d_to, d_to_ext, min_layover, max_layover, arrive_by, depart_after, fares=None):
    fares = fares or api.day_flights
    legs = [fares(path[i], path[i + 1], d_from, d_to if i == 0 else d_to_ext) for i in range(len(path) - 1)]
    return stitch(legs, min_layover, max_layover, arrive_by, depart_after)


def _key(it):
    return (*(f.orig for f in it), it[-1].dest), it[0].local("dep").date()


def resolve(itineraries, top, max_price=None, price=None):
    """Cheapest itinerary per (route, departure day), with every fare exact.

    Flights other than a day's cheapest carry a lower-bound price. Itineraries
    are expanded best-first: the cheapest one by lower bound has its unknown
    fares looked up and goes back on the heap with its real total. When an
    itinerary comes off the heap fully priced, nothing left can beat it. Only
    flights that could make the top results are ever priced.
    """
    price = price or api.price_flight
    heap = [(sum(f.price for f in it), n, it) for n, it in enumerate(itineraries)]
    heapq.heapify(heap)
    counter, results, seen = len(heap), [], set()
    with ThreadPoolExecutor(max_workers=4) as pool:
        while heap and len(results) < top:
            total, _, it = heapq.heappop(heap)
            if max_price is not None and total > max_price:
                break
            if _key(it) in seen:
                continue
            if all(f.exact for f in it):
                seen.add(_key(it))
                results.append((total, it))
                continue
            unknown = [f for f in it if not f.exact]
            exact = dict(zip(unknown, pool.map(price, unknown)))
            if any(p is None for p in exact.values()):
                continue  # sold out
            priced = tuple(api.Flight(f.orig, f.dest, f.dep, f.arr, exact[f]) if f in exact else f for f in it)
            heapq.heappush(heap, (sum(f.price for f in priced), counter, priced))
            counter += 1
    return sorted(results, key=lambda x: (x[0], x[1][-1].arr - x[1][0].dep))
