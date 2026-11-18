# Fuel Route Planner (Django)

A Django REST API that takes a start and a finish location in the USA and returns:

- the driving route (GeoJSON) plus a link to an interactive map of it,
- the most cost-effective places to refuel along the way, for a vehicle with a **500-mile range** at **10 mpg** (a 50-gallon tank),
- the total money spent on fuel.

It makes **one external API call per trip**: a single routing request to the free [OSRM](https://project-osrm.org/) server. Geocoding and station lookups happen offline, and repeat requests come from cache with **zero** external calls.

---

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python manage.py migrate
python manage.py load_stations       # imports + geocodes the fuel price CSV (~1 s)
python manage.py runserver
```

- API: <http://127.0.0.1:8000/api/route/?start=New%20York,%20NY&finish=Los%20Angeles,%20CA>
- Map UI: <http://127.0.0.1:8000/?start=New%20York,%20NY&finish=Los%20Angeles,%20CA>
- Tests: `python manage.py test fuel`
- A Postman collection is in [`postman/`](postman/fuel-route.postman_collection.json).

Requires Python 3.12+ (Django 6.1).

---

## API

### `GET /api/route/` or `POST /api/route/`

| Parameter | Required | Default | Description |
|---|---|---|---|
| `start` | yes | | `"City, ST"` (e.g. `Chicago, IL`), `"lat,lon"`, or a free-form US address |
| `finish` | yes | | Same formats as `start` |
| `start_fuel_gallons` | no | `50` (full tank) | Fuel in the tank when leaving |
| `stop_penalty` | no | `5` | USD "cost" charged per stop when optimising; `0` gives the pure lowest fuel bill |
| `corridor_miles` | no | `8` | How far from the route a station may be |
| `include_geometry` | no | `true` | Include the route's GeoJSON line |

```bash
curl "http://127.0.0.1:8000/api/route/?start=Chicago,%20IL&finish=Denver,%20CO"

curl -X POST http://127.0.0.1:8000/api/route/ \
     -H "Content-Type: application/json" \
     -d '{"start": "Chicago, IL", "finish": "Denver, CO", "start_fuel_gallons": 12.5}'
```

Response (shortened):

```json
{
  "start":  {"query": "Chicago, IL", "name": "Chicago, IL", "lat": 41.837, "lon": -87.685, "source": "gazetteer"},
  "finish": {"query": "Denver, CO",  "name": "Denver, CO",  "lat": 39.762, "lon": -104.881, "source": "gazetteer"},
  "route": {
    "distance_miles": 1002.7,
    "duration_hours": 17.79,
    "geometry": {"type": "LineString", "coordinates": [[-87.68, 41.83], "..."]}
  },
  "fuel_plan": {
    "assumptions": {"max_range_miles": 500, "miles_per_gallon": 10, "tank_gallons": 50,
                    "start_fuel_gallons": 50, "corridor_miles": 8, "stop_penalty_usd": 5},
    "stops": [
      {"stop": 1, "station": {"name": "KUM & GO #0370", "city": "Gretna", "state": "NE", "...": "..."},
       "price_per_gallon": 2.9207, "route_mile": 483.0, "gallons": 5.9, "cost": 17.24, "...": "..."},
      {
        "stop": 2,
        "station": {"opis_id": 68368, "name": "AKAL TRAVEL CENTER", "address": "I-80 EX 360",
                    "city": "Waco", "state": "NE", "lat": 40.897, "lon": -97.4617},
        "price_per_gallon": 2.799,
        "route_mile": 559.0,
        "distance_off_route_miles": 5.2,
        "fuel_on_arrival_gallons": 0.0,
        "gallons": 44.37,
        "cost": 124.18
      }
    ],
    "number_of_stops": 2,
    "total_gallons_purchased": 50.27,
    "total_fuel_cost": 141.42,
    "average_price_paid": 2.8133,
    "fuel_used_gallons": 100.27,
    "fuel_left_at_finish_gallons": 0.0
  },
  "meta": {"cached": false, "external_api_calls": ["osrm"], "stations_in_corridor": 39,
           "route_ms": 79.0, "compute_ms": 81.2},
  "map_url": "http://127.0.0.1:8000/?start=Chicago%2C+IL&finish=Denver%2C+CO"
}
```

`map_url` opens a Leaflet/OpenStreetMap page showing the route, start and finish, and numbered fuel stops with prices. The page reuses the cached plan, so it makes no extra routing call.

Errors: `400` for invalid input or a location outside the USA, `422` when no stations in the corridor can cover a gap on the route, `502` when the routing service is unavailable.

---

## How it works

```
start, finish
   │  offline geocoding (Census gazetteer + ZIP centroids, 52k US places)
   ▼
OSRM /route  ── 1 HTTP call (cached 24 h)
   │  full-resolution polyline → resampled every 0.5 mi with cumulative mileage
   ▼
corridor search (numpy)  → stations within 8 mi of the route, with their route mile
   ▼
cheapest station per 25-mile segment
   ▼
exact dynamic-programming optimiser  → stops, gallons, cost
```

### 1. Station data (`python manage.py load_stations`)
- The CSV has 8,151 rows. The 620 Canadian rows are skipped, and the remaining rows collapse to **6,626 unique US truck stops**; several rows share an OPIS id, and the lowest price is kept.
- Stations have no coordinates, only "I-44, EXIT 283" style addresses. Geocoding ~6.6k addresses through a free API would be slow and rate-limited. Instead each station is matched to its town in a bundled table (`data/us_places.csv`) built from the US Census 2023 Gazetteer (places + county subdivisions) and GeoNames US postal codes. Names are normalised so `Saint Johns` = `St. Johns`, `Mc Calla` = `McCalla`, and so on. **100% of stations match.** To rebuild the table, see `scripts/build_places.py`.
- `"City, ST"` API inputs are resolved with the same table. Nominatim is only called as a fallback for free-form addresses.

### 2. Routing: one call
OSRM's public server is free and needs no key. We request `overview=full` geometry once and cache it. Everything after that is local computation.

### 3. Corridor search
All stations sit in memory as numpy arrays, loaded once per process. The route is processed in ~100-mile windows. Each window compares its points only against the stations inside its own padded bounding box, so the work grows with route length, not with the size of the US. NY→LA (2,800 mi, ~6.6k stations) takes about **40 ms**.

Stations are placed at their town centre, which is often a few miles from the actual interstate exit. That is why the corridor defaults to 8 miles. The response reports each stop's approximate distance from the route.

### 4. Optimiser (`fuel/optimizer.py`)
This is the "gas station problem": fixed route, known prices, a tank that can be partly filled. It is solved **exactly** with the dynamic program from Khuller, Malekian & Mestre, *"To Fill or not to Fill: The Gas Station Problem"* (2011). Some optimal plan, at every stop, either fills the tank or buys just enough to reach the next stop. As a result, the only fuel levels you can arrive at a station with are `0`, `500 − distance from an earlier station`, or what is left of the starting fuel. That makes the state space small, and the solve takes milliseconds.

The pure minimum-cost plan is technically optimal but impractical. On NY→LA it makes 15 stops, several of them 10 miles apart to save cents. So by default each stop also "costs" **$5**:

| NY → LA (2,810 mi) | stops | fuel cost |
|---|---|---|
| `stop_penalty=0` (pure minimum) | 15 | $698.74 |
| `stop_penalty=5` (default) | 6 | $705.74 |

The tests cross-check the DP (with `stop_penalty=0`) against the classic greedy algorithm on 300 random routes, and check that no plan ever overfills the tank or runs it dry.

### Performance
| | time |
|---|---|
| First request (OSRM round-trip, which dominates) | ~0.1–0.7 s |
| Local work for a coast-to-coast plan | ~20–50 ms |
| Repeat request (cached) | ~1–3 ms |

---

## Assumptions
- The vehicle **starts with a full tank** (pass `start_fuel_gallons` to change this). `total_fuel_cost` is the money spent at the recommended stops; `fuel_used_gallons` is the trip's total fuel use. A trip shorter than 500 miles therefore needs no stop and costs $0 en route.
- The plan aims to arrive at the finish with an empty tank, buying no fuel that the trip doesn't use.
- Detours off the route to reach a station are reported but not added to the distance driven.
- The price list is treated as current. The lowest listed price is used for a truck stop that appears more than once.

## Project layout
```
config/                 Django settings & URLs
fuel/
  models.py             FuelStation
  management/commands/load_stations.py
  geo.py                distances, name normalisation, offline gazetteer
  routing.py            OSRM + Nominatim fallback (counted & cached)
  stations.py           in-memory numpy station index + corridor search
  optimizer.py          exact DP optimiser (+ greedy reference)
  services.py           orchestration: geocode → route → corridor → plan
  serializers.py / views.py / templates/fuel/map.html
  tests/                optimiser, geo/corridor and API tests (OSRM mocked)
data/                   fuel price CSV + generated US places table
scripts/build_places.py rebuilds data/us_places.csv
postman/                Postman collection
```

## Configuration
Environment variables (all optional): `OSRM_BASE_URL` (point at a self-hosted OSRM in production), `NOMINATIM_URL`, `FUEL_MAX_RANGE_MILES`, `FUEL_MPG`, `FUEL_DEFAULT_CORRIDOR_MILES`, `FUEL_SEGMENT_MILES`, `FUEL_DEFAULT_STOP_PENALTY`, `ROUTE_CACHE_SECONDS`, `PLAN_CACHE_SECONDS`, `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`.

The cache is in-process (LocMem). For multiple workers, switch `CACHES` to Redis.

## Data credits
US Census Bureau 2023 Gazetteer files (public domain). [GeoNames](https://www.geonames.org/) postal codes (CC BY 4.0). Routing © OSRM / OpenStreetMap contributors (ODbL).
