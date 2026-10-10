# Changelog

## 0.1.0 — 2026-10-10

Initial release.

- Search a whole country (`--from DE`) or a list of airports for the cheapest way to a destination.
- Direct flights plus self-transfer connections with up to 2 stops.
- Layovers checked in real time zones, with minimum/maximum layover limits.
- `--arrive-by` / `--depart-after` to fit around an existing booking.
- On-disk response cache (3 h) and a `curl` fallback for Pythons without CA certificates.
- Shows the cheapest direct flight and how much each connection saves against it.
- CSV export.
