"""Geographic helpers: distances, place-name normalisation and an offline
US place gazetteer used to geocode both the fuel stations and the API inputs
without calling any external service.
"""

from __future__ import annotations

import csv
import difflib
import functools
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

EARTH_RADIUS_MILES = 3958.7613
MILES_PER_DEGREE = EARTH_RADIUS_MILES * math.pi / 180.0  # ~69.09

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}
_STATE_BY_NAME = {name.lower(): abbr for abbr, name in US_STATES.items()}

# Rough bounding boxes (lat_min, lat_max, lon_min, lon_max) for the USA.
_US_BOXES = (
    (24.3, 49.5, -125.0, -66.8),   # contiguous states
    (51.0, 71.6, -180.0, -129.9),  # Alaska
    (18.8, 22.4, -160.5, -154.7),  # Hawaii
)

# Abbreviations applied token-by-token so "Saint Johns" == "St. Johns",
# "Mc Calla" == "McCalla", "S Coffeyville" == "South Coffeyville", etc.
_TOKEN_SUBS = {
    "saint": "st", "sainte": "ste", "mount": "mt", "fort": "ft",
    "north": "n", "south": "s", "east": "e", "west": "w", "and": "",
}


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def in_usa(lat: float, lon: float) -> bool:
    return any(a <= lat <= b and c <= lon <= d for a, b, c, d in _US_BOXES)


def normalize_name(name: str) -> str:
    """Canonical lookup key for a US place name."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    tokens = re.findall(r"[a-z0-9]+", text.lower().replace("&", " and "))
    return "".join(_TOKEN_SUBS.get(t, t) for t in tokens)


def state_abbr(value: str) -> str | None:
    value = value.strip()
    if value.upper() in US_STATES:
        return value.upper()
    return _STATE_BY_NAME.get(value.lower())


@dataclass(frozen=True)
class Place:
    name: str
    state: str
    lat: float
    lon: float


class PlaceGazetteer:
    """In-memory index of ~45k US places (Census 2023 gazetteer), keyed by
    (state, normalised name). Built by scripts/build_places.py."""

    def __init__(self, path: Path):
        self._by_state: dict[str, dict[str, Place]] = {}
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                place = Place(row["name"], row["state"], float(row["lat"]), float(row["lon"]))
                self._by_state.setdefault(row["state"], {}).setdefault(row["key"], place)

    def lookup(self, city: str, state: str, fuzzy_cutoff: float | None = None) -> Place | None:
        places = self._by_state.get(state.upper())
        if not places:
            return None
        key = normalize_name(city)
        if key in places:
            return places[key]
        if fuzzy_cutoff:
            match = difflib.get_close_matches(key, places.keys(), n=1, cutoff=fuzzy_cutoff)
            if match:
                return places[match[0]]
        return None


@functools.lru_cache(maxsize=1)
def get_gazetteer() -> PlaceGazetteer:
    return PlaceGazetteer(Path(settings.PLACES_CSV))
