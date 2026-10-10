"""Offline tests: no network access needed."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from ryanair_connect import api
from ryanair_connect.search import candidate_paths, cheapest_per_day, parse_places, stitch

api.AIRPORTS.update({
    "CGN": {"name": "Cologne", "country": "DE", "tz": "Europe/Berlin"},
    "FKB": {"name": "Karlsruhe", "country": "DE", "tz": "Europe/Berlin"},
    "STN": {"name": "London Stansted", "country": "GB", "tz": "Europe/London"},
    "PMI": {"name": "Palma", "country": "ES", "tz": "Europe/Madrid"},
    "MRS": {"name": "Marseille", "country": "FR", "tz": "Europe/Paris"},
    "RAK": {"name": "Marrakesh", "country": "MA", "tz": "Africa/Casablanca"},
})


def fl(orig, dest, dep, arr, price):
    return api.Flight(orig, dest, api.to_utc(dep, orig), api.to_utc(arr, dest), price)


H = timedelta(hours=1)


class TimezoneTest(unittest.TestCase):
    def test_local_times_are_converted_per_airport(self):
        # CGN 21:40 (UTC+2) -> STN 22:10 (UTC+1) is a 1h30 flight, not 30 minutes
        f = fl("CGN", "STN", "2026-10-15T21:40:00", "2026-10-15T22:10:00", 20)
        self.assertEqual(f.arr - f.dep, 1.5 * H)

    def test_layover_uses_utc(self):
        a = fl("CGN", "STN", "2026-10-15T21:40:00", "2026-10-15T22:10:00", 20)
        b = fl("STN", "MRS", "2026-10-16T07:30:00", "2026-10-16T10:30:00", 52)
        self.assertEqual(b.dep - a.arr, 9 * H + 20 * timedelta(minutes=1))


class StitchTest(unittest.TestCase):
    def setUp(self):
        self.leg1 = [fl("CGN", "STN", "2026-10-15T21:40:00", "2026-10-15T22:10:00", 20)]
        self.leg2 = [
            fl("STN", "MRS", "2026-10-15T23:00:00", "2026-10-16T02:00:00", 10),  # too tight
            fl("STN", "MRS", "2026-10-16T07:30:00", "2026-10-16T10:30:00", 52),  # ok
            fl("STN", "MRS", "2026-10-18T07:30:00", "2026-10-18T10:30:00", 5),   # too long
        ]

    def test_only_realistic_layovers(self):
        its = stitch([self.leg1, self.leg2], 3 * H, 30 * H)
        self.assertEqual(len(its), 1)
        self.assertEqual(its[0][1].price, 52)

    def test_arrive_by(self):
        deadline = datetime(2026, 10, 16, 10, 0, tzinfo=api.tz_of("MRS")).astimezone(api.UTC)
        self.assertEqual(stitch([self.leg1, self.leg2], 3 * H, 30 * H, arrive_by=deadline), [])

    def test_depart_after(self):
        after = datetime(2026, 10, 15, 22, 0, tzinfo=api.tz_of("CGN")).astimezone(api.UTC)
        self.assertEqual(stitch([self.leg1, self.leg2], 3 * H, 30 * H, depart_after=after), [])

    def test_empty_leg(self):
        self.assertEqual(stitch([self.leg1, []], 3 * H, 30 * H), [])

    def test_cheapest_per_day_ranks_by_price(self):
        cheap = (fl("FKB", "PMI", "2026-10-14T18:00:00", "2026-10-14T20:00:00", 10),
                 fl("PMI", "MRS", "2026-10-15T05:50:00", "2026-10-15T07:10:00", 18))
        pricey = stitch([self.leg1, self.leg2], 3 * H, 30 * H)[0]
        ranked = cheapest_per_day([pricey, cheap])
        self.assertEqual([round(t, 2) for t, _ in ranked], [28, 72])
        self.assertEqual(cheapest_per_day([pricey, cheap], max_price=50)[0][0], 28)


class GraphTest(unittest.TestCase):
    def test_parse_places(self):
        self.assertEqual(parse_places("DE", api.AIRPORTS), {"CGN", "FKB"})
        self.assertEqual(parse_places("mrs, stn", api.AIRPORTS), {"MRS", "STN"})

    def test_candidate_paths(self):
        network = {
            "CGN": {"STN": 20, "PMI": 60, "MRS": 170},
            "STN": {"MRS": 25, "RAK": 30},
            "PMI": {"MRS": 18},
            "RAK": {"MRS": 40},
            "MRS": {"CGN": 170, "STN": 25, "PMI": 18, "RAK": 40},  # routes run both ways
        }
        with ThreadPoolExecutor(2) as pool:
            paths = candidate_paths({"CGN"}, {"MRS"}, 2, date(2026, 10, 1), date(2026, 10, 2), 10, pool,
                                    cheapest=lambda a, *_: network.get(a, {}),
                                    routes=lambda a: set(network.get(a, {})))
        self.assertEqual(paths[0], ("CGN", "MRS"))  # direct first
        self.assertEqual(paths[1], ("CGN", "STN", "MRS"))  # then cheapest connection
        self.assertIn(("CGN", "STN", "RAK", "MRS"), paths)


if __name__ == "__main__":
    unittest.main()
