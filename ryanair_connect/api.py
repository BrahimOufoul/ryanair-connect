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
from datetime import date, datetime
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
    price: float

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


_fares = {}


def day_fares(o, d, d_from, d_to):
    """Cheapest flight per day (with times) on route o -> d."""
    key = (o, d, d_from, d_to)
    if key in _fares:
        return _fares[key]
    flights = []
    m = date(d_from.year, d_from.month, 1)
    while m <= d_to:
        url = (f"https://www.ryanair.com/api/farfnd/v4/oneWayFares/{o}/{d}/cheapestPerDay"
               f"?outboundMonthOfDate={m.isoformat()}&currency={CURRENCY}")
        data = get_json(url) or {}
        for f in (data.get("outbound") or {}).get("fares", []):
            if f.get("soldOut") or f.get("unavailable") or not f.get("price") or not f.get("departureDate"):
                continue
            if d_from <= date.fromisoformat(f["day"]) <= d_to:
                flights.append(Flight(o, d, to_utc(f["departureDate"], o), to_utc(f["arrivalDate"], d), f["price"]["value"]))
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    _fares[key] = flights
    return flights


def booking_link(f):
    return (f"https://www.ryanair.com/gb/en/trip/flights/select?adults=1&teens=0&children=0&infants=0"
            f"&dateOut={f.local('dep').date()}&isReturn=false&originIata={f.orig}&destinationIata={f.dest}")
