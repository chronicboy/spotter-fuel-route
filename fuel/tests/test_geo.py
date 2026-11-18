import numpy as np
from django.test import SimpleTestCase

from fuel.geo import get_gazetteer, haversine_miles, normalize_name
from fuel.routing import ApiCallCounter, GeocodingError, geocode
from fuel.stations import build_index


class NormalizeTests(SimpleTestCase):
    def test_equivalent_spellings(self):
        self.assertEqual(normalize_name("Saint Johns"), normalize_name("St. Johns"))
        self.assertEqual(normalize_name("Mc Calla"), normalize_name("McCalla"))
        self.assertEqual(normalize_name("S Coffeyville"), normalize_name("South Coffeyville"))
        self.assertEqual(normalize_name("Fort Wayne  "), normalize_name("Ft. Wayne"))


class GazetteerTests(SimpleTestCase):
    def test_lookups(self):
        gz = get_gazetteer()
        for city, state in [("Saint Louis", "MO"), ("Oklahoma City", "OK"), ("Nashville", "TN"),
                            ("Ruther Glen", "VA"), ("Mc Calla", "AL")]:
            with self.subTest(city=city):
                self.assertIsNotNone(gz.lookup(city, state))
        chicago = gz.lookup("Chicago", "IL")
        self.assertLess(haversine_miles(chicago.lat, chicago.lon, 41.88, -87.63), 10)

    def test_geocode_inputs_offline(self):
        counter = ApiCallCounter()
        loc = geocode("Denver, Colorado", counter)
        self.assertEqual(loc.source, "gazetteer")
        loc = geocode("41.88, -87.63", counter)
        self.assertEqual(loc.source, "coordinates")
        self.assertEqual(counter.calls, [])

    def test_coordinates_outside_usa_rejected(self):
        with self.assertRaises(GeocodingError):
            geocode("51.5, -0.12", ApiCallCounter())  # London


class CorridorTests(SimpleTestCase):
    def test_near_route_filters_and_positions(self):
        # Route due east along latitude 40 from lon -100 to -95 (~265 mi).
        lon = np.linspace(-100, -95, 600)
        lat = np.full_like(lon, 40.0)
        miles = np.concatenate(([0], np.cumsum(
            [haversine_miles(40, a, 40, b) for a, b in zip(lon[:-1], lon[1:])])))
        index = build_index([
            {"latitude": 40.03, "longitude": -99.0, "price": 3.1},   # ~2 mi off, near mile 53
            {"latitude": 40.5, "longitude": -98.0, "price": 2.9},    # ~35 mi off
            {"latitude": 40.0, "longitude": -96.0, "price": 3.3},    # on route, near mile 212
            {"latitude": 40.0, "longitude": -90.0, "price": 2.5},    # far east of the route
        ])
        hits = {i: (m, off) for i, m, off in index.near_route(lat, lon, miles, corridor_miles=8)}
        self.assertEqual(set(hits), {0, 2})
        self.assertAlmostEqual(hits[0][0], haversine_miles(40, -100, 40, -99), delta=1)
        self.assertAlmostEqual(hits[0][1], 2.07, delta=0.2)
        self.assertAlmostEqual(hits[2][0], haversine_miles(40, -100, 40, -96), delta=1)
