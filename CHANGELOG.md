# Changelog

## 0.2.0 — 2026-10-10

- Every scheduled flight is now considered, not only the cheapest flight of each day. Flights come from the published timetable and are priced on demand through a lazy best-first search, so only flights that could make the top results are looked up.
- All one-stop connections are always checked; `--candidates` now only limits two-stop paths.

## 0.1.0 — 2026-10-10

Initial release.

- Search a whole country (`--from DE`) or a list of airports for the cheapest way to a destination.
- Direct flights plus self-transfer connections with up to 2 stops.
- Layovers checked in real time zones, with minimum/maximum layover limits.
- `--arrive-by` / `--depart-after` to fit around an existing booking.
- On-disk response cache (3 h) and a `curl` fallback for Pythons without CA certificates.
- Shows the cheapest direct flight and how much each connection saves against it.
- CSV export.
