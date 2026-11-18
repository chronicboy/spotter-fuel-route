"""External services: OSRM (routing) and, only as a fallback, Nominatim
(geocoding). Both are free and keyless. Every call is counted and cached so
a repeat request makes zero external calls."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.cache import cache

from .geo import get_gazetteer, in_usa, state_abbr

log = logging.getLogger(__name__)

_session = requests.Session()
_LATLON_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


class GeocodingError(Exception):
    pass


class RoutingError(Exception):
    pass


@dataclass
class Location:
    query: str
    name: str
    lat: float
    lon: float
    source: str  # "coordinates" | "gazetteer" | "nominatim"

    def as_dict(self):
        return {"query": self.query, "name": self.name, "lat": round(self.lat, 6),
                "lon": round(self.lon, 6), "source": self.source}


class ApiCallCounter:
    def __init__(self):
        self.calls: list[str] = []

    def add(self, name: str):
        self.calls.append(name)


def geocode(query: str, counter: ApiCallCounter) -> Location:
    """Resolve a "lat,lon", "City, ST" or free-form US address."""
    query = query.strip()

    m = _LATLON_RE.match(query)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if not in_usa(lat, lon):
            raise GeocodingError(f"'{query}' is not inside the USA (expected 'lat,lon').")
        return Location(query, f"{lat:.5f}, {lon:.5f}", lat, lon, "coordinates")

    if "," in query:
        city, _, state = query.rpartition(",")
        abbr = state_abbr(state)
        if abbr:
            place = get_gazetteer().lookup(city, abbr)
            if place:
                return Location(query, f"{place.name}, {abbr}", place.lat, place.lon, "gazetteer")

    return _nominatim(query, counter)


def _nominatim(query: str, counter: ApiCallCounter) -> Location:
    key = "geocode:" + hashlib.sha1(query.lower().encode()).hexdigest()
    cached = cache.get(key)
    if cached:
        return Location(query, *cached, source="nominatim")

    counter.add("nominatim")
    try:
        resp = _session.get(
            settings.NOMINATIM_URL,
            params={"q": query, "format": "jsonv2", "countrycodes": "us", "limit": 1},
            headers={"User-Agent": settings.HTTP_USER_AGENT},
            timeout=settings.HTTP_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        results = resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("Nominatim failed for %r: %s", query, exc)
        raise GeocodingError(f"Could not geocode '{query}': geocoding service unavailable.") from exc

    if not results:
        raise GeocodingError(
            f"Could not find '{query}' in the USA. Try 'City, ST' or 'lat,lon'."
        )
    hit = results[0]
    lat, lon = float(hit["lat"]), float(hit["lon"])
    if not in_usa(lat, lon):
        raise GeocodingError(f"'{query}' resolved outside the USA.")
    name = hit.get("display_name", query)
    cache.set(key, (name, lat, lon), settings.ROUTE_CACHE_SECONDS)
    return Location(query, name, lat, lon, "nominatim")


def fetch_route(start: Location, finish: Location, counter: ApiCallCounter) -> dict:
    """One OSRM call -> full-resolution route geometry, distance and duration."""
    key = "osrm:%.5f,%.5f;%.5f,%.5f" % (start.lon, start.lat, finish.lon, finish.lat)
    cached = cache.get(key)
    if cached:
        return cached

    url = "%s/route/v1/driving/%.6f,%.6f;%.6f,%.6f" % (
        settings.OSRM_BASE_URL.rstrip("/"), start.lon, start.lat, finish.lon, finish.lat,
    )
    counter.add("osrm")
    try:
        resp = _session.get(
            url,
            params={"overview": "full", "geometries": "geojson", "steps": "false",
                    "alternatives": "false"},
            headers={"User-Agent": settings.HTTP_USER_AGENT},
            timeout=settings.HTTP_TIMEOUT_SECONDS,
        )
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("OSRM request failed: %s", exc)
        raise RoutingError("Routing service unavailable, please retry.") from exc

    if resp.status_code != 200 or data.get("code") != "Ok" or not data.get("routes"):
        raise RoutingError(f"No drivable route found ({data.get('message') or data.get('code')}).")

    route = data["routes"][0]
    result = {
        "coordinates": route["geometry"]["coordinates"],  # [[lon, lat], ...]
        "distance_m": route["distance"],
        "duration_s": route["duration"],
    }
    cache.set(key, result, settings.ROUTE_CACHE_SECONDS)
    return result
