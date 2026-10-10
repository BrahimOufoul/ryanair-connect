"""Thin, cached client for the public endpoints behind ryanair.com.

Only the Python standard library is used. Responses are cached on disk so a
repeated search costs Ryanair almost nothing.
"""
import hashlib
import json
import os
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
CACHE_DIR = os.environ.get("RYANAIR_CONNECT_CACHE", os.path.join(os.path.expanduser("~"), ".cache", "ryanair-connect"))
CACHE_TTL = 3 * 3600  # fares are cached for 3 hours
CURRENCY = "EUR"
UTC = ZoneInfo("UTC")

AIRPORTS = {}  # IATA code -> {"name", "country", "tz"}


# --------------------------------------------------------------------------- HTTP
_use_curl = False


def _fetch(url):
    """Download a URL. Falls back to `curl` when Python has no CA certificates (common on macOS)."""
    global _use_curl
    if not _use_curl:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read().decode()
        except urllib.error.URLError as e:
            if not isinstance(e.reason, ssl.SSLError):
                raise
            _use_curl = True
    r = subprocess.run(["curl", "-sS", "--fail-with-body", "-m", "20", "-A", UA, "-H", "Accept: application/json", url],
                       check=False, capture_output=True, text=True)
    if r.returncode == 22:
        raise urllib.error.HTTPError(url, 404, "not found", None, None)
    if r.returncode:
        raise RuntimeError(r.stderr)
    return r.stdout


def get_json(url, ttl=CACHE_TTL):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, hashlib.sha1(url.encode()).hexdigest() + ".json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        with open(path) as f:
            return json.load(f)
    for attempt in range(4):
        try:
            data = json.loads(_fetch(url))
            with open(path, "w") as f:
                json.dump(data, f)
            return data
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
            time.sleep(2 * (attempt + 1))
        except (urllib.error.URLError, OSError, RuntimeError, ValueError):
            time.sleep(2 * (attempt + 1))
    return None


# --------------------------------------------------------------------------- data
@dataclass(frozen=True)
class Flight:
    orig: str
    dest: str
    dep: datetime  # UTC
    arr: datetime  # UTC
    price: float  # exact fare, or the day's cheapest fare on the route as a lower bound
    exact: bool = True

    def local(self, which):
        code = self.orig if which == "dep" else self.dest
        t = self.dep if which == "dep" else self.arr
        return t.astimezone(tz_of(code))


def tz_of(code):
    return ZoneInfo(AIRPORTS.get(code, {}).get("tz", "UTC"))


def to_utc(local_iso, code):
    """Ryanair returns local wall-clock times without an offset; attach the airport's zone."""
    return datetime.fromisoformat(local_iso).replace(tzinfo=tz_of(code)).astimezone(UTC)


def load_airports():
    data = get_json("https://www.ryanair.com/api/views/locate/5/airports/en/active", ttl=7 * 86400) or []
    for a in data:
        AIRPORTS[a["code"]] = {
            "name": a["name"],
            "country": a["country"]["code"].upper(),
            "tz": a.get("timeZone") or "UTC",
        }
    return AIRPORTS


_routes = {}


def routes(code):
    """Set of airports served directly from `code`."""
    if code not in _routes:
        data = get_json(f"https://www.ryanair.com/api/views/locate/searchWidget/routes/en/airport/{code}", ttl=86400) or []
        _routes[code] = {r["arrivalAirport"]["code"] for r in data}
    return _routes[code]


_cheapest = {}


def cheapest_from(origin, d_from, d_to):
    """Cheapest fare to every destination from `origin` in a date range: {dest: price}. One request per airport."""
    key = (origin, d_from, d_to)
    if key not in _cheapest:
        url = ("https://services-api.ryanair.com/farfnd/3/oneWayFares"
               f"?departureAirportIataCode={origin}&outboundDepartureDateFrom={d_from}"
               f"&outboundDepartureDateTo={d_to}&currency={CURRENCY}&market=en-gb&limit=1000")
        data = get_json(url) or {}
        _cheapest[key] = {f["outbound"]["arrivalAirport"]["iataCode"]: f["outbound"]["price"]["value"]
                          for f in data.get("fares", [])}
    return _cheapest[key]


def _months(d_from, d_to):
    m = date(d_from.year, d_from.month, 1)
    while m <= d_to:
        yield m
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)


def cheapest_per_day(o, d, d_from, d_to):
    """{day: (local departure 'HH:MM', price)} for days with seats left on route o -> d."""
    out = {}
    for m in _months(d_from, d_to):
        url = (f"https://www.ryanair.com/api/farfnd/v4/oneWayFares/{o}/{d}/cheapestPerDay"
               f"?outboundMonthOfDate={m.isoformat()}&currency={CURRENCY}")
        for f in ((get_json(url) or {}).get("outbound") or {}).get("fares", []):
            if f.get("soldOut") or f.get("unavailable") or not f.get("price") or not f.get("departureDate"):
                continue
            day = date.fromisoformat(f["day"])
            if d_from <= day <= d_to:
                out[day] = (f["departureDate"], f["arrivalDate"], f["price"]["value"])
    return out


def timetable(o, d, d_from, d_to):
    """{day: [(departure 'HH:MM', arrival 'HH:MM'), ...]}: every scheduled flight, not just the cheapest."""
    out = {}
    for m in _months(d_from, d_to):
        url = f"https://www.ryanair.com/api/timtbl/3/schedules/{o}/{d}/years/{m.year}/months/{m.month}"
        for day in (get_json(url, ttl=86400) or {}).get("days", []):
            dt = date(m.year, m.month, day["day"])
            if d_from <= dt <= d_to:
                out[dt] = [(f["departureTime"], f["arrivalTime"]) for f in day.get("flights", [])]
    return out


_flights = {}


def day_flights(o, d, d_from, d_to):
    """Every flight on route o -> d in the date range.

    The fare feed only prices the cheapest flight of each day. Every other
    scheduled flight that day costs at least that much, so it gets the day's
    cheapest fare as a lower bound (`exact=False`) and is priced on demand.
    """
    key = (o, d, d_from, d_to)
    if key in _flights:
        return _flights[key]
    fares, sched = cheapest_per_day(o, d, d_from, d_to), timetable(o, d, d_from, d_to)
    flights = []
    for day, (dep_iso, arr_iso, price) in sorted(fares.items()):
        cheapest = Flight(o, d, to_utc(dep_iso, o), to_utc(arr_iso, d), price)
        flights.append(cheapest)
        for dep_hm, arr_hm in sched.get(day, []):
            dep_local = datetime.combine(day, datetime.strptime(dep_hm, "%H:%M").time())
            if dep_local.isoformat(timespec="minutes") == dep_iso[:16]:
                continue
            arr_local = datetime.combine(day, datetime.strptime(arr_hm, "%H:%M").time())
            dep, arr = to_utc(dep_local.isoformat(), o), to_utc(arr_local.isoformat(), d)
            if arr <= dep:  # lands after midnight
                arr = to_utc((arr_local + timedelta(days=1)).isoformat(), d)
            flights.append(Flight(o, d, dep, arr, price, exact=False))
    _flights[key] = flights
    return flights


_prices = {}


def price_flight(f):
    """Exact fare of one specific flight, or None if it is sold out."""
    key = (f.orig, f.dest, f.dep)
    if key not in _prices:
        dl = f.local("dep")
        url = ("https://www.ryanair.com/api/farfnd/v4/oneWayFares"
               f"?departureAirportIataCode={f.orig}&arrivalAirportIataCode={f.dest}"
               f"&outboundDepartureDateFrom={dl.date()}&outboundDepartureDateTo={dl.date()}"
               f"&outboundDepartureTimeFrom={dl:%H:%M}&outboundDepartureTimeTo={dl:%H:%M}"
               f"&currency={CURRENCY}&market=en-gb")
        fares = (get_json(url) or {}).get("fares", [])
        wanted = dl.strftime("%Y-%m-%dT%H:%M")
        match = [x["outbound"]["price"]["value"] for x in fares if x["outbound"]["departureDate"][:16] == wanted]
        _prices[key] = match[0] if match else None
    return _prices[key]


def booking_link(f):
    return (f"https://www.ryanair.com/gb/en/trip/flights/select?adults=1&teens=0&children=0&infants=0"
            f"&dateOut={f.local('dep').date()}&isReturn=false&originIata={f.orig}&destinationIata={f.dest}")
