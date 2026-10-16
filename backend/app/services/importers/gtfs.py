"""Read public transport stops from a GTFS feed.

BKK publishes Budapest's timetable as a free GTFS feed, and most cities do
the same. Its ``stops.txt`` lists every platform and metro entrance with
coordinates, which is more complete than OpenStreetMap's stop tags. Only the
stop locations are read: the transit access check needs where people board,
not the timetable.
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

from app.models.schemas import BoundingBox, Coordinates, TransitStop

GTFS_SOURCE = "GTFS feed (operator licence, e.g. BKK open data)"
# location_type: empty or 0 is a stop or platform, 2 is a station entrance.
BOARDING_LOCATION_TYPES = {"", "0", "2"}


def _mode(vehicle_type: str | None) -> str:
    """Mode from the GTFS route type code that some feeds put on stops."""
    if not vehicle_type:
        return "other"
    try:
        code = int(vehicle_type)
    except ValueError:
        return "other"
    if code in {3, 11} or 700 <= code < 900:
        return "bus"
    if code in {0, 5} or 900 <= code < 1000:
        return "tram"
    if code == 1 or 400 <= code < 500:
        return "subway"
    if code in {2, 12} or 100 <= code < 200:
        return "rail"
    if code == 4 or 1000 <= code < 1100 or 1200 <= code < 1300:
        return "ferry"
    return "other"


def parse_gtfs_stops(stops_txt: str) -> list[TransitStop]:
    """Boarding points from the text of a GTFS ``stops.txt``."""
    stops: list[TransitStop] = []
    for row in csv.DictReader(io.StringIO(stops_txt.lstrip("﻿"))):
        if (row.get("location_type") or "").strip() not in BOARDING_LOCATION_TYPES:
            continue
        try:
            lat = float(row["stop_lat"])
            lon = float(row["stop_lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
            continue
        stop_id = (row.get("stop_id") or "").strip()
        if not stop_id:
            continue
        stops.append(
            TransitStop(
                stop_id=stop_id[:100],
                mode=_mode((row.get("vehicle_type") or "").strip()),
                name=(row.get("stop_name") or "").strip()[:300] or None,
                coordinates=Coordinates(lat=lat, lon=lon),
            )
        )
    return stops


def read_gtfs_stops(path: str | Path) -> list[TransitStop]:
    """Stops from a GTFS zip or a bare ``stops.txt``."""
    source = Path(path)
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            names = [name for name in archive.namelist() if name.rsplit("/", 1)[-1] == "stops.txt"]
            if not names:
                raise ValueError(f"{source} has no stops.txt")
            text = archive.read(names[0]).decode("utf-8-sig")
    else:
        text = source.read_text(encoding="utf-8-sig")
    return parse_gtfs_stops(text)


def stops_in_bbox(
    stops: list[TransitStop], bbox: BoundingBox, margin_deg: float = 0.005
) -> list[TransitStop]:
    """Stops inside the area, plus a margin so edge blocks see stops just outside."""
    return [
        stop
        for stop in stops
        if bbox.south - margin_deg <= stop.coordinates.lat <= bbox.north + margin_deg
        and bbox.west - margin_deg <= stop.coordinates.lon <= bbox.east + margin_deg
    ][:100_000]
