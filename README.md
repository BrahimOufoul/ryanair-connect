# ryanair-connect

[![tests](https://github.com/BrahimOufoul/ryanair-connect/actions/workflows/tests.yml/badge.svg)](https://github.com/BrahimOufoul/ryanair-connect/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen)

**Find the cheap Ryanair trips that Ryanair won't show you.**

Ryanair sells point-to-point tickets. If the direct flight is expensive, the website stops there. It won't tell you that a second Ryanair flight through another city can cut the price in half. And if there is no direct route at all, it shows you nothing.

`ryanair-connect` searches the route *graph* instead of the route list. It checks every origin airport in a country, every possible hub (up to two stops) and every day in your date range. Then it only joins flights whose layover is realistic in real time zones, and ranks the result by total price.

```
$ ryanair-connect --from CGN --to LPA --date-from 2026-12-18 --date-to 2026-12-21 --max-layover 12

  Cheapest direct flight: €381.99  (CGN → LPA, Sat 19 Dec)

#1  € 171.46   CGN → VLC → LPA   (13h20 total, 1 stop)   saves €210.53 (-55%)
     CGN Sat 19 Dec 20:30 → VLC 22:55   € 126.99
         ⏱  layover in VLC: 7h50
     VLC Sun 20 Dec 06:45 → LPA 08:50   €  44.47

#2  € 182.98   CGN → BGY → LPA   (13h50 total, 1 stop)   saves €199.01 (-52%)
     CGN Mon 21 Dec 06:00 → BGY 07:25   €  37.99
         ⏱  layover in BGY: 8h00
     BGY Mon 21 Dec 15:25 → LPA 18:50   € 144.99
```
*Real output: Cologne → Gran Canaria the weekend before Christmas. **€210 cheaper** than the direct flight. Allow longer layovers (the default is up to 30 h, overnight included) and it finds €136.98 via Marrakesh, 64% below the direct fare.*

### More real results

| Trip | Direct | With one stop | Saved |
|---|---:|---:|---:|
| Cologne → Gran Canaria, Christmas | €381.99 | **€171.46** via Valencia | **−55%** |
| Cologne → Gran Canaria, mid-November | €127.99 | **€42.38** via Valencia | **−67%** |
| Berlin → Bari, Christmas | €171.99 | **€79.88** via Budapest | **−54%** |
| Berlin → Paphos, Christmas | €156.99 | **€78.98** via Bergamo | **−50%** |
| Berlin → Seville | no direct route | **€33.98** via Palma | – |

*Fares were checked in October 2026 and change constantly. The pattern doesn't: direct flights on busy dates are priced for people who don't look further.*

## Quick start

There is nothing to install except Python 3.9+. The tool only uses the standard library.

```bash
git clone https://github.com/BrahimOufoul/ryanair-connect.git
cd ryanair-connect
python3 -m ryanair_connect --from CGN --to LPA --date-from 2026-12-18 --date-to 2026-12-21 --max-layover 12
```

Or install it as a command:

```bash
pip install .
ryanair-connect --help
```

## Usage

```bash
# Every Ryanair airport in Germany → Barcelona, up to 1 stop
ryanair-connect --from DE --to BCN --date-from 2026-11-01 --date-to 2026-11-20

# Several origins, any airport in Italy, max €60 in total
ryanair-connect --from BER,HAM --to IT --date-from 2026-11-01 --date-to 2026-11-30 --max-price 60

# Fit around a flight you already booked: leave after 16:00 on Thursday,
# be in Seville by 12:00 on Saturday, allow two stops and export to CSV
ryanair-connect --from BER --to SVQ \
    --date-from 2026-11-12 --date-to 2026-11-14 \
    --depart-after "2026-11-12 16:00" --arrive-by "2026-11-14 12:00" \
    --max-stops 2 --csv results.csv
```

### Options

| Option | Description | Default |
|---|---|---|
| `--from` | Origin: IATA codes (`CGN,DUS`) or a country code (`DE`) | required |
| `--to` | Destination: IATA codes (`MRS`) or a country code (`FR`) | required |
| `--date-from` / `--date-to` | Date range for the first flight (`YYYY-MM-DD`) | required |
| `--arrive-by` | Latest arrival, local time at the destination | – |
| `--depart-after` | Earliest departure, local time at the origin | – |
| `--max-stops` | Maximum number of stops: `0`, `1` or `2` | `1` |
| `--min-layover` | Minimum layover in hours | `3` |
| `--max-layover` | Maximum layover in hours, overnight included | `30` |
| `--max-price` | Only show itineraries up to this total (EUR) | – |
| `--top` | Number of results to show | `20` |
| `--csv` | Also write the results to a CSV file | – |

## How it works

1. **Load the network.** It fetches every active Ryanair airport with its IANA time zone, plus the routes from each airport.
2. **Find candidate paths cheaply.** One "cheapest fare to everywhere" request per airport gives a lower-bound price for every leg. From these it builds every `A → X → B` and `A → X → Y → B` path and keeps the most promising ones. This keeps the number of requests small.
3. **Check real flights.** For each candidate it downloads the actual daily fares with departure and arrival times. Ryanair returns local wall-clock times without an offset, so every time is converted to UTC through the airport's time zone before layovers are compared. Otherwise a Berlin → London → Seville connection would be off by an hour.
4. **Rank.** It keeps the cheapest itinerary per route and day, sorted by total price, then by travel time.

Responses are cached on disk for 3 hours in `~/.cache/ryanair-connect`, which you can override with `RYANAIR_CONNECT_CACHE`. A repeated search costs Ryanair almost nothing.

```
ryanair_connect/
  api.py       cached HTTP client, airports, routes, fares, time zones
  search.py    route-graph search and layover stitching (pure, unit-tested)
  cli.py       argument parsing and output
tests/         offline unit tests, run in CI on Python 3.9–3.13
```

## Limitations

- **Ryanair only.** A connection exists only if Ryanair flies both legs.
- **One flight per route per day.** The fare feed only returns the cheapest flight each day, so a slightly pricier flight at a better time can be missed.
- **Same-airport connections only.** Changing between two airports in one city, such as Stansted → Luton, is not considered.
- **Base fares only.** Prices are for one adult with a small under-seat bag. Every leg is its own booking, so checked bags are paid, and re-checked, per flight.
- **Prices move.** Fares are cached for hours and can change in minutes. Ryanair's own page is always the truth.

## ⚠️ Self-transfer means separate tickets

If your first flight is late and you miss the second one, nobody owes you a seat, a refund or a hotel. That risk is why these trips are cheap. Leave a generous buffer, which is why the default minimum layover is 3 hours, and check the entry rules for every country you connect through. A self-transfer in London, for example, means entering the UK, which requires an ETA.

## Disclaimer

This is an unofficial hobby project. It is **not affiliated with, endorsed by or connected to Ryanair**. It uses undocumented endpoints of the public Ryanair website, which can change or break at any time. Ryanair's terms prohibit automated collection and resale of its fares. This tool is intended for personal use at a polite request rate. Please don't run it in a loop or build a commercial service on it.

## Contributing

Issues and pull requests are welcome. Please run the tests before submitting:

```bash
python3 -m unittest discover -s tests -v
```

## License

[MIT](LICENSE) © Brahim Oufoul
