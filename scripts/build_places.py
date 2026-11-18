"""Build data/us_places.csv from the US Census 2023 Gazetteer files plus the
GeoNames US postal-code file.

The output is a compact (state, key, name, lat, lon) table used to geocode
fuel stations and "City, ST" API inputs offline. It is committed to the repo,
so this script only needs to be re-run to refresh the data:

    curl -O https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_place_national.zip
    curl -O https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_cousubs_national.zip
    curl -O https://download.geonames.org/export/zip/US.zip
    (unzip all three)
    python scripts/build_places.py 2023_Gaz_place_national.txt 2023_Gaz_cousubs_national.txt US.txt

Priority on name clashes: Census place > county subdivision > postal city.
"""

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fuel.geo import US_STATES, haversine_miles, normalize_name  # noqa: E402

# Trailing legal/statistical descriptors used in gazetteer NAME values,
# longest first so "charter township" wins over "township".
DESCRIPTORS = sorted(
    [
        "city and borough", "consolidated government", "metropolitan government",
        "metro government", "unified government", "urban county", "charter township",
        "city", "town", "village", "borough", "cdp", "township", "ccd", "municipality",
        "plantation", "ut", "gore", "grant", "location", "purchase", "district",
        "precinct", "corporation", "county",
    ],
    key=len,
    reverse=True,
)


def core_name(raw: str) -> str:
    name = re.sub(r"\s*\(.*?\)\s*", " ", raw).strip()
    lower = name.lower()
    for d in DESCRIPTORS:
        if lower.endswith(" " + d):
            return name[: -len(d) - 1].strip()
    return name


def read(path: str, priority: int):
    with open(path, encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter="\t")
        header = [h.strip() for h in next(reader)]
        for row in reader:
            rec = dict(zip(header, (c.strip() for c in row)))
            if rec["USPS"] not in US_STATES:
                continue
            core = core_name(rec["NAME"])
            lat, lon = float(rec["INTPTLAT"]), float(rec["INTPTLONG"])
            aland = int(rec["ALAND"])
            yield rec["USPS"], normalize_name(core), core, lat, lon, priority, aland
            # Aliases for consolidated cities, e.g. "Nashville-Davidson" -> "Nashville".
            for sep in ("-", "/"):
                if sep in core:
                    alias = core.split(sep)[0].strip()
                    yield rec["USPS"], normalize_name(alias), alias, lat, lon, priority + 10, aland


def read_postal(path: str, priority: int):
    """GeoNames US postal codes: postal city names (incl. unincorporated
    communities like 'Ruther Glen, VA'), averaged over their ZIP codes."""
    groups: dict[tuple[str, str], list] = {}
    with open(path, encoding="utf-8") as fh:
        for row in csv.reader(fh, delimiter="\t"):
            state, name = row[4], row[2]
            if state not in US_STATES or not row[9]:
                continue
            groups.setdefault((state, name), []).append((float(row[9]), float(row[10])))
    for (state, name), pts in groups.items():
        lat = sum(p[0] for p in pts) / len(pts)
        lon = sum(p[1] for p in pts) / len(pts)
        yield state, normalize_name(name), name, lat, lon, priority, 0


def main(place_file: str, cousub_file: str, postal_file: str | None = None) -> None:
    best: dict[tuple[str, str], tuple] = {}
    postal: dict[tuple[str, str], tuple[float, float]] = {}
    sources = [read(place_file, 0), read(cousub_file, 1)]
    if postal_file:
        sources.append(read_postal(postal_file, 2))
    for source in sources:
        for state, key, name, lat, lon, p, aland in source:
            if not key:
                continue
            if p == 2:
                postal[(state, key)] = (lat, lon)
            rank = (p, -aland)
            cur = best.get((state, key))
            if cur is None or rank < cur[0]:
                best[(state, key)] = (rank, name, lat, lon)

    # Census "internal points" can land far from the populated core for
    # oddly shaped places (San Francisco's falls in the Pacific because the
    # city includes the Farallon Islands). Where the ZIP-code centroid
    # disagrees by more than 10 miles, trust the ZIP centroid.
    moved = 0
    for k, (rank, name, lat, lon) in best.items():
        if k in postal and haversine_miles(lat, lon, *postal[k]) > 10:
            best[k] = (rank, name, *postal[k])
            moved += 1
    print(f"Replaced {moved} Census points with ZIP-code centroids")

    out = ROOT / "data" / "us_places.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["state", "key", "name", "lat", "lon"])
        for (state, key), (_, name, lat, lon) in sorted(best.items()):
            w.writerow([state, key, name, f"{lat:.6f}", f"{lon:.6f}"])
    print(f"Wrote {len(best)} places to {out}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
