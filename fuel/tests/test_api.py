from decimal import Decimal
from unittest import mock

import numpy as np
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from fuel.geo import get_gazetteer, haversine_miles
from fuel.models import FuelStation
from fuel.stations import get_station_index


def fake_osrm_response(start, finish, points=400):
    lats = np.linspace(start.lat, finish.lat, points)
    lons = np.linspace(start.lon, finish.lon, points)
    meters = haversine_miles(start.lat, start.lon, finish.lat, finish.lon) * 1609.344
    resp = mock.Mock(status_code=200)
    resp.json.return_value = {
        "code": "Ok",
        "routes": [{
            "distance": meters,
            "duration": meters / 29.0,
            "geometry": {"type": "LineString", "coordinates": np.column_stack((lons, lats)).tolist()},
        }],
    }
    return resp


class RouteApiTests(TestCase):
    """End-to-end through the view, with OSRM mocked (straight-line route)."""

    def setUp(self):
        cache.clear()
        get_station_index.cache_clear()
        gz = get_gazetteer()
        self.start, self.finish = gz.lookup("Chicago", "IL"), gz.lookup("Denver", "CO")
        # Stations every ~10% of the way, on the straight line between the cities.
        for k, frac in enumerate(np.linspace(0.05, 0.95, 10)):
            FuelStation.objects.create(
                opis_id=k + 1, name=f"STOP {k + 1}", address="I-80", city="X", state="NE",
                price=Decimal("3.50") - Decimal(k % 3) / 10,
                latitude=self.start.lat + frac * (self.finish.lat - self.start.lat),
                longitude=self.start.lon + frac * (self.finish.lon - self.start.lon),
            )
        get_station_index.cache_clear()
        self.client = APIClient()
        patcher = mock.patch(
            "fuel.routing._session.get",
            return_value=fake_osrm_response(self.start, self.finish),
        )
        self.http_get = patcher.start()
        self.addCleanup(patcher.stop)

    def test_plan_route_get(self):
        res = self.client.get("/api/route/", {"start": "Chicago, IL", "finish": "Denver, CO"})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        fp = body["fuel_plan"]
        self.assertGreater(body["route"]["distance_miles"], 800)
        self.assertGreaterEqual(fp["number_of_stops"], 1)
        self.assertAlmostEqual(fp["total_fuel_cost"], sum(s["cost"] for s in fp["stops"]), delta=0.05)
        self.assertEqual(body["route"]["geometry"]["type"], "LineString")
        self.assertIn("map_url", body)
        # Exactly one external call: the route. Geocoding is offline.
        self.assertEqual(body["meta"]["external_api_calls"], ["osrm"])
        self.assertEqual(self.http_get.call_count, 1)

    def test_repeat_request_is_cached(self):
        params = {"start": "Chicago, IL", "finish": "Denver, CO"}
        self.client.get("/api/route/", params)
        res = self.client.get("/api/route/", params)
        self.assertTrue(res.json()["meta"]["cached"])
        self.assertEqual(self.http_get.call_count, 1)

    def test_post_with_options(self):
        res = self.client.post("/api/route/", {
            "start": "Chicago, IL", "finish": "Denver, CO",
            "start_fuel_gallons": 20, "stop_penalty": 0, "include_geometry": False,
        }, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertIsNone(body["route"]["geometry"])
        self.assertEqual(body["fuel_plan"]["assumptions"]["start_fuel_gallons"], 20)

    def test_validation_errors(self):
        self.assertEqual(self.client.get("/api/route/", {"start": "Chicago, IL"}).status_code, 400)
        res = self.client.get("/api/route/", {"start": "Chicago, IL", "finish": "Denver, CO",
                                              "start_fuel_gallons": 80})
        self.assertEqual(res.status_code, 400)

    def test_non_us_coordinates_rejected(self):
        res = self.client.get("/api/route/", {"start": "48.85,2.35", "finish": "Denver, CO"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.http_get.call_count, 0)

    def test_infeasible_when_no_stations(self):
        FuelStation.objects.all().delete()
        get_station_index.cache_clear()
        res = self.client.get("/api/route/", {"start": "Chicago, IL", "finish": "Denver, CO"})
        self.assertEqual(res.status_code, 422)

    def test_map_page_renders(self):
        res = self.client.get("/", {"start": "Chicago, IL", "finish": "Denver, CO"})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "plan-data")
        self.assertContains(res, "STOP")
