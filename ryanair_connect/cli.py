"""Command-line interface."""
import argparse
import csv
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from . import __version__, api
from .search import candidate_paths, cheapest_per_day, itineraries_for, parse_places

EXAMPLES = """examples:
  # every Ryanair airport in Germany -> Barcelona, up to 1 stop
  ryanair-connect --from DE --to BCN --date-from 2026-11-01 --date-to 2026-11-20

  # leave Berlin after 16:00, be in Seville by 12:00 two days later, up to 2 stops
  ryanair-connect --from BER --to SVQ --date-from 2026-11-12 --date-to 2026-11-14 \\
      --depart-after "2026-11-12 16:00" --arrive-by "2026-11-14 12:00" --max-stops 2
"""


def fmt_dur(td):
    h, m = divmod(int(td.total_seconds() // 60), 60)
    return f"{h}h{m:02d}"


def parse_local(value, codes):
    """Interpret 'YYYY-MM-DD HH:MM' in the timezone of the first airport in `codes`."""
    if not value:
        return None
    code = min(codes) if codes else None
    return datetime.fromisoformat(value).replace(tzinfo=api.tz_of(code)).astimezone(api.UTC)


def build_parser():
    ap = argparse.ArgumentParser(
        prog="ryanair-connect",
        description="Find cheap Ryanair itineraries, including self-transfer connections Ryanair does not sell.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--from", dest="orig", required=True, help="origin: IATA codes (CGN,DUS) or a country code (DE)")
    ap.add_argument("--to", dest="dest", required=True, help="destination: IATA codes (MRS) or a country code (FR)")
    ap.add_argument("--date-from", required=True, help="earliest departure day, YYYY-MM-DD")
    ap.add_argument("--date-to", required=True, help="latest departure day of the first flight, YYYY-MM-DD")
    ap.add_argument("--max-stops", type=int, default=1, choices=[0, 1, 2], help="maximum number of stops (default: 1)")
    ap.add_argument("--min-layover", type=float, default=3, help="minimum layover in hours (default: 3)")
    ap.add_argument("--max-layover", type=float, default=30, help="maximum layover in hours, overnight included (default: 30)")
    ap.add_argument("--arrive-by", help='latest arrival, local time at destination, e.g. "2026-10-16 14:10"')
    ap.add_argument("--depart-after", help='earliest departure, local time at origin, e.g. "2026-10-15 16:00"')
    ap.add_argument("--max-price", type=float, help="only show itineraries up to this total price (EUR)")
    ap.add_argument("--candidates", type=int, default=80, help="connecting routes checked in detail (default: 80)")
    ap.add_argument("--top", type=int, default=20, help="number of results to show (default: 20)")
    ap.add_argument("--csv", help="also write results to this CSV file")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return ap


def print_results(ranked, direct=None):
    print(f"\n{'=' * 78}\n TOP {len(ranked)} CHEAPEST ITINERARIES\n{'=' * 78}")
    if direct:
        f = direct[1][0]
        print(f"\n  Cheapest direct flight: €{direct[0]:.2f}  ({f.orig} → {f.dest}, {f.local('dep'):%a %d %b})")
    else:
        print("\n  No direct flight in this date range.")
    for n, (total, it) in enumerate(ranked, 1):
        route = " → ".join([it[0].orig] + [f.dest for f in it])
        stops = len(it) - 1
        saving = ""
        if direct and stops and total < direct[0]:
            saving = f"   saves €{direct[0] - total:.2f} (-{(1 - total / direct[0]) * 100:.0f}%)"
        print(f"\n#{n}  €{total:7.2f}   {route}   ({fmt_dur(it[-1].arr - it[0].dep)} total, "
              f"{stops} stop{'s' if stops != 1 else ''}){saving}")
        for i, f in enumerate(it):
            dl, al = f.local("dep"), f.local("arr")
            print(f"     {f.orig} {dl:%a %d %b %H:%M} → {f.dest} {al:%H:%M}   €{f.price:7.2f}")
            if i + 1 < len(it):
                print(f"         ⏱  layover in {f.dest}: {fmt_dur(it[i + 1].dep - f.arr)}")
        for f in it:
            print(f"     Book: {api.booking_link(f)}")


def write_csv(path, ranked):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "total_eur", "route", "travel_time", "flight_1", "flight_2", "flight_3"])
        for n, (total, it) in enumerate(ranked, 1):
            w.writerow([n, f"{total:.2f}", "-".join([it[0].orig] + [f.dest for f in it]), fmt_dur(it[-1].arr - it[0].dep)]
                       + [f"{f.orig}-{f.dest} {f.local('dep'):%Y-%m-%d %H:%M} {f.price:.2f}" for f in it])


def main(argv=None):
    args = build_parser().parse_args(argv)

    api.load_airports()
    if not api.AIRPORTS:
        sys.exit("Could not load the Ryanair airport list (are you online?).")
    origins, dests = parse_places(args.orig, api.AIRPORTS), parse_places(args.dest, api.AIRPORTS)
    d_from, d_to = date.fromisoformat(args.date_from), date.fromisoformat(args.date_to)
    min_lay, max_lay = timedelta(hours=args.min_layover), timedelta(hours=args.max_layover)
    d_to_ext = d_to + timedelta(days=int(args.max_layover * args.max_stops // 24) + 1)
    arrive_by = parse_local(args.arrive_by, dests)
    depart_after = parse_local(args.depart_after, origins)

    print(f"From: {', '.join(sorted(origins))}")
    print(f"To:   {', '.join(sorted(dests))}")
    print(f"Searching the route network (max {args.max_stops} stop(s)) ...", flush=True)

    with ThreadPoolExecutor(max_workers=6) as pool:
        paths = candidate_paths(origins, dests, args.max_stops, d_from, d_to_ext, args.candidates, pool)
        print(f"Checking real daily fares and times on {len(paths)} routes ...", flush=True)
        found = []
        def check(path):
            return itineraries_for(path, d_from, d_to, d_to_ext, min_lay, max_lay, arrive_by, depart_after)

        for its in pool.map(check, paths):
            found.extend(its)

    ranked = cheapest_per_day(found, args.max_price)[: args.top]
    direct = next(iter(cheapest_per_day([it for it in found if len(it) == 1])), None)
    if not ranked:
        print("\nNo matching itinerary found. Try a wider date range, --max-stops 2 or a larger --max-layover.")
        return

    print_results(ranked, direct)
    if args.csv:
        write_csv(args.csv, ranked)
        print(f"\nSaved CSV: {args.csv}")

    print("\nNote: base fares for one adult with a small cabin bag, fetched live from Ryanair; they can change.\n"
          "Self-transfer = separate tickets: if the first flight is late, nobody owes you the connection.\n"
          "Check passport/visa rules for every country you connect in (e.g. UK ETA).")


if __name__ == "__main__":
    main()
