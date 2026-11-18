"""Trip planning: geocode -> one routing call -> corridor search -> optimiser."""

from __future__ import annotations

import hashlib
import json
import time

import numpy as np
from django.conf import settings
from django.core.cache import cache

from .geo import EARTH_RADIUS_MILES
from .optimizer import Candidate, plan_fuel_stops
from .routing import ApiCallCounter, fetch_route, geocode
from .stations import get_station_index

METERS_PER_MILE = 1609.344
SAMPLE_SPACING_MILES = 0.5


def _cumulative_miles(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    p = np.radians(lat)
    dp = np.diff(p)
    dl = np.radians(np.diff(lon))
    a = np.sin(dp / 2) ** 2 + np.cos(p[:-1]) * np.cos(p[1:]) * np.sin(dl / 2) ** 2
    seg = 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    return np.concatenate(([0.0], np.cumsum(seg)))


def _resample(cum: np.ndarray, spacing: float) -> np.ndarray:
    """Indices of route vertices roughly `spacing` miles apart (keeps ends)."""
    if len(cum) < 2 or cum[-1] <= 0:
        return np.arange(len(cum))
    targets = np.arange(0.0, cum[-1], spacing)
    idx = np.searchsorted(cum, targets)
    return np.unique(np.concatenate((idx, [len(cum) - 1])))


def _cheapest_per_segment(candidates: list[Candidate], segment_miles: float) -> list[Candidate]:
    """Keep the cheapest station in each `segment_miles` stretch of route.

    The optimiser is exactly cost-optimal, which on dense corridors means
    hopping between stations a few miles apart to save cents. Collapsing each
    segment to its cheapest station keeps plans practical (fewer stops) at a
    negligible cost difference. 0 disables.
    """
    if segment_miles <= 0:
        return candidates
    best: dict[int, Candidate] = {}
    for c in candidates:
        key = int(c.mile // segment_miles)
        cur = best.get(key)
        if cur is None or (c.price, c.ref[1]) < (cur.price, cur.ref[1]):
            best[key] = c
    return list(best.values())


def _station_dict(rec: dict) -> dict:
    return {
        "opis_id": rec["opis_id"],
        "name": rec["name"],
        "address": rec["address"],
        "city": rec["city"],
        "state": rec["state"],
        "lat": rec["latitude"],
        "lon": rec["longitude"],
    }


def plan_trip(
    start: str,
    finish: str,
    start_fuel_gallons: float | None = None,
    corridor_miles: float | None = None,
    stop_penalty: float | None = None,
    include_geometry: bool = True,
) -> dict:
    t0 = time.perf_counter()
    max_range = settings.FUEL_MAX_RANGE_MILES
    mpg = settings.FUEL_MPG
    tank_gallons = max_range / mpg
    if start_fuel_gallons is None:
        start_fuel_gallons = tank_gallons
    if corridor_miles is None:
        corridor_miles = settings.FUEL_DEFAULT_CORRIDOR_MILES
    if stop_penalty is None:
        stop_penalty = settings.FUEL_DEFAULT_STOP_PENALTY

    params = [start.strip().lower(), finish.strip().lower(), round(start_fuel_gallons, 3),
              round(corridor_miles, 3), round(stop_penalty, 3), include_geometry]
    cache_key = "plan:" + hashlib.sha1(json.dumps(params).encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached:
        result = dict(cached)
        result["meta"] = {**cached["meta"], "cached": True, "external_api_calls": [],
                          "compute_ms": round((time.perf_counter() - t0) * 1000, 1)}
        return result

    counter = ApiCallCounter()
    origin = geocode(start, counter)
    destination = geocode(finish, counter)
    route = fetch_route(origin, destination, counter)
    t_route = time.perf_counter()

    coords = np.asarray(route["coordinates"], dtype=float)
    lon, lat = coords[:, 0], coords[:, 1]
    cum = _cumulative_miles(lat, lon)
    total_miles = route["distance_m"] / METERS_PER_MILE
    if cum[-1] > 0:
        cum *= total_miles / cum[-1]  # align geometry length with OSRM's distance
    sample = _resample(cum, SAMPLE_SPACING_MILES)
    s_lat, s_lon, s_cum = lat[sample], lon[sample], cum[sample]

    index = get_station_index()
    hits = index.near_route(s_lat, s_lon, s_cum, corridor_miles)
    candidates = _cheapest_per_segment(
        [Candidate(mile=mile, price=float(index.price[i]), ref=(i, off)) for i, mile, off in hits],
        settings.FUEL_SEGMENT_MILES,
    )
    plan = plan_fuel_stops(
        total_miles, candidates, max_range, mpg,
        start_fuel_miles=start_fuel_gallons * mpg, stop_penalty=stop_penalty,
    )

    stops = []
    for n, p in enumerate(plan.purchases, 1):
        station_i, off = p.candidate.ref
        stops.append({
            "stop": n,
            "station": _station_dict(index.records[station_i]),
            "price_per_gallon": round(p.candidate.price, 4),
            "route_mile": round(p.candidate.mile, 1),
            "distance_off_route_miles": round(off, 1),
            "fuel_on_arrival_gallons": round(p.fuel_on_arrival_gallons, 2),
            "gallons": round(p.gallons, 2),
            "cost": round(p.cost, 2),
        })

    result = {
        "start": origin.as_dict(),
        "finish": destination.as_dict(),
        "route": {
            "distance_miles": round(total_miles, 1),
            "duration_hours": round(route["duration_s"] / 3600, 2),
            "geometry": {
                "type": "LineString",
                "coordinates": np.round(np.column_stack((s_lon, s_lat)), 5).tolist(),
            } if include_geometry else None,
        },
        "fuel_plan": {
            "assumptions": {
                "max_range_miles": max_range,
                "miles_per_gallon": mpg,
                "tank_gallons": tank_gallons,
                "start_fuel_gallons": round(start_fuel_gallons, 2),
                "corridor_miles": corridor_miles,
                "stop_penalty_usd": stop_penalty,
            },
            "stops": stops,
            "number_of_stops": len(stops),
            "total_gallons_purchased": round(plan.total_gallons, 2),
            "total_fuel_cost": round(plan.total_cost, 2),
            "average_price_paid": round(plan.total_cost / plan.total_gallons, 4)
            if plan.total_gallons else None,
            "fuel_used_gallons": round(plan.fuel_used_gallons, 2),
            "fuel_left_at_finish_gallons": round(plan.fuel_left_gallons, 2),
        },
        "meta": {
            "cached": False,
            "external_api_calls": counter.calls,
            "stations_in_corridor": len(candidates),
            "route_ms": round((t_route - t0) * 1000, 1),
            "compute_ms": round((time.perf_counter() - t0) * 1000, 1),
        },
    }
    cache.set(cache_key, result, settings.PLAN_CACHE_SECONDS)
    return result
