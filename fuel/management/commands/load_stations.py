"""Load the fuel price CSV into the database, geocoding every truck stop
offline against the bundled Census place gazetteer (no API calls)."""

import csv
from collections import Counter
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from fuel.geo import US_STATES, get_gazetteer
from fuel.models import FuelStation
from fuel.stations import get_station_index


class Command(BaseCommand):
    help = "Import fuel prices CSV and geocode stations to their town centroid."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", nargs="?", default=str(settings.FUEL_PRICES_CSV))
        parser.add_argument(
            "--fuzzy-cutoff", type=float, default=0.85,
            help="difflib ratio used when a town name has no exact match (0 disables).",
        )

    def handle(self, csv_path, fuzzy_cutoff, **options):
        path = Path(csv_path)
        if not path.exists():
            raise CommandError(f"CSV not found: {path}")

        gazetteer = get_gazetteer()
        stats = Counter()
        best: dict[int, dict] = {}

        with open(path, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                stats["rows"] += 1
                state = row["State"].strip().upper()
                if state not in US_STATES:
                    stats["skipped_non_us"] += 1
                    continue
                opis_id = int(row["OPIS Truckstop ID"])
                price = Decimal(row["Retail Price"].strip())
                current = best.get(opis_id)
                if current is None or price < current["price"]:
                    best[opis_id] = {
                        "opis_id": opis_id,
                        "name": row["Truckstop Name"].strip(),
                        "address": row["Address"].strip(),
                        "city": row["City"].strip(),
                        "state": state,
                        "rack_id": int(row["Rack ID"]) if row["Rack ID"].strip() else None,
                        "price": price,
                    }

        stations, unmatched = [], []
        for rec in best.values():
            place = gazetteer.lookup(rec["city"], rec["state"])
            if place is None and fuzzy_cutoff:
                place = gazetteer.lookup(rec["city"], rec["state"], fuzzy_cutoff=fuzzy_cutoff)
                if place:
                    stats["fuzzy_matched"] += 1
            if place is None:
                unmatched.append(f'{rec["city"]}, {rec["state"]}')
                continue
            stations.append(FuelStation(latitude=place.lat, longitude=place.lon, **rec))

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations, batch_size=1000)
        get_station_index.cache_clear()

        self.stdout.write(
            f"Rows read: {stats['rows']} | non-US rows skipped: {stats['skipped_non_us']} | "
            f"unique US stations: {len(best)}"
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {len(stations)} stations "
                f"({stats['fuzzy_matched']} via fuzzy town match, {len(unmatched)} not geocoded)."
            )
        )
        if unmatched:
            self.stdout.write("Not geocoded: " + "; ".join(sorted(set(unmatched))))
