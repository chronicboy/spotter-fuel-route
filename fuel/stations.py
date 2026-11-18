"""In-memory, numpy-backed index of all fuel stations.

~6.7k stations fit comfortably in memory; loading them once per process
avoids a DB query per request and makes the corridor search a few
vectorised array operations.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

import numpy as np

from .geo import MILES_PER_DEGREE
from .models import FuelStation


@dataclass
class StationIndex:
    lat: np.ndarray
    lon: np.ndarray
    price: np.ndarray
    records: list[dict]

    def __len__(self) -> int:
        return len(self.records)

    def near_route(
        self,
        route_lat: np.ndarray,
        route_lon: np.ndarray,
        route_miles: np.ndarray,
        corridor_miles: float,
        window: int = 200,
    ) -> list[tuple[int, float, float]]:
        """Stations within `corridor_miles` of the route polyline.

        `route_*` are densely sampled route vertices (~0.5 mi apart) and
        `route_miles` their cumulative distance from the start. Returns
        (station_index, mile_along_route, miles_off_route) tuples.

        The route is processed in windows of `window` vertices (~100 mi);
        each window only measures distances to the stations inside its own
        padded bounding box, so work stays proportional to route length.
        """
        if not len(self) or not len(route_lat):
            return []

        pad_lat = corridor_miles / MILES_PER_DEGREE
        best_off = np.full(len(self), np.inf)
        best_vertex = np.zeros(len(self), dtype=int)

        for start in range(0, len(route_lat), window):
            w_lat = route_lat[start:start + window]
            w_lon = route_lon[start:start + window]
            max_abs_lat = float(np.max(np.abs(w_lat))) + pad_lat
            pad_lon = corridor_miles / (MILES_PER_DEGREE * max(np.cos(np.radians(max_abs_lat)), 0.05))
            idx = np.nonzero(
                (self.lat >= w_lat.min() - pad_lat) & (self.lat <= w_lat.max() + pad_lat)
                & (self.lon >= w_lon.min() - pad_lon) & (self.lon <= w_lon.max() + pad_lon)
            )[0]
            if not len(idx):
                continue
            # Equirectangular distance: well under 1% error at these scales.
            s_lat, s_lon = self.lat[idx][:, None], self.lon[idx][:, None]
            cos_lat = np.cos(np.radians((s_lat + w_lat[None, :]) * 0.5))
            d2 = (s_lat - w_lat[None, :]) ** 2 + ((s_lon - w_lon[None, :]) * cos_lat) ** 2
            nearest = np.argmin(d2, axis=1)
            dist = np.sqrt(d2[np.arange(len(idx)), nearest]) * MILES_PER_DEGREE
            better = dist < best_off[idx]
            best_off[idx[better]] = dist[better]
            best_vertex[idx[better]] = nearest[better] + start

        hits = np.nonzero(best_off <= corridor_miles)[0]
        return [
            (int(i), float(route_miles[best_vertex[i]]), float(best_off[i])) for i in hits
        ]


def build_index(stations) -> StationIndex:
    records = list(stations)
    return StationIndex(
        lat=np.array([s["latitude"] for s in records], dtype=float),
        lon=np.array([s["longitude"] for s in records], dtype=float),
        price=np.array([float(s["price"]) for s in records], dtype=float),
        records=records,
    )


@functools.lru_cache(maxsize=1)
def get_station_index() -> StationIndex:
    return build_index(
        FuelStation.objects.values(
            "opis_id", "name", "address", "city", "state", "price", "latitude", "longitude"
        )
    )
