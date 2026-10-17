"""Download the open data layers the plan and the simulator can use.

Writes plain files into an output folder; point the settings at them:

    POPULATION_PATH=<out>/population.tif         Meta HRSL, 30 m (CC BY 4.0); set
    POPULATION_SOURCE="Meta High Resolution Settlement Layer (CC BY 4.0)" 
    JOBS_PATH=<out>/jobs.csv                     estimated from Overture places and buildings
    FACILITIES_PATH=<out>/facilities.csv         Overture places
    TURN_RESTRICTIONS_PATH=<out>/turn_restrictions.json   OSM rules via Overture
    GTFS_PATH=<out>/gtfs.zip                     the operator's feed (BKK for Budapest)
    AIR_QUALITY_STATIONS_PATH=<out>/no2_stations.csv      OpenAQ archive, annual NO2

Every source is a public bucket or URL that needs no account. Overture reads
need ``pyarrow`` (``pip install pyarrow``); it is not a dependency of the app.

    python scripts/fetch_open_data.py --bbox 18.925,47.350,19.335,47.613 --out data/budapest \
        --gtfs-url https://go.bkk.hu/api/static/v1/public-gtfs/budapest_gtfs.zip --year 2025

Jobs are a proxy: each Overture place counts as a workplace with a typical
number of jobs for its category, and large non-residential buildings with a
known use add jobs by floor space. Only the spread of jobs over the city
matters to the simulator, not their total.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OVERTURE_RELEASE = "2026-09-23.1"
OVERTURE_BUCKET = "overturemaps-us-west-2/release"
HRSL_VRT = (
    "/vsicurl/https://dataforgood-fb-data.s3.amazonaws.com/hrsl-cogs/"
    "hrsl_general/hrsl_general-latest.vrt"
)
OPENAQ = "https://openaq-data-archive.s3.amazonaws.com/"

# Typical jobs per establishment by Overture basic category (fallback 4).
JOBS_PER_PLACE = {
    "corporate_or_business_office": 25,
    "professional_service": 8,
    "financial_service": 12,
    "manufacturer": 30,
    "education": 25,
    "school": 40,
    "college_university": 120,
    "hospital": 300,
    "government_office": 40,
    "hotel": 20,
    "restaurant": 8,
    "shopping": 10,
    "storage_facility": 6,
}
# Square metres of floor space per job by building use.
FLOOR_PER_JOB = {
    "office": 20,
    "commercial": 35,
    "retail": 40,
    "industrial": 80,
    "warehouse": 150,
    "school": 60,
    "university": 50,
    "hospital": 40,
    "public": 30,
}
FACILITY_CATEGORIES = {
    "fire_station": "fire_station",
    "fire_department": "fire_station",
    "ambulance_and_ems_services": "ambulance_station",
    "emergency_medical_service": "ambulance_station",
    "elementary_school": "school",
    "high_school": "school",
    "middle_school": "school",
    "school": "school",
    "preschool": "kindergarten",
    "kindergarten": "kindergarten",
    "day_care_preschool": "kindergarten",
}


def _overture(theme: str, kind: str, columns: list[str], bbox, extra=None):
    import pyarrow.compute as pc
    import pyarrow.dataset as ds
    import pyarrow.fs as pfs

    west, south, east, north = bbox
    fs = pfs.S3FileSystem(anonymous=True, region="us-west-2")
    dataset = ds.dataset(
        f"{OVERTURE_BUCKET}/{OVERTURE_RELEASE}/theme={theme}/type={kind}/",
        filesystem=fs,
        format="parquet",
    )
    condition = (
        (pc.field("bbox", "xmin") < east)
        & (pc.field("bbox", "xmax") > west)
        & (pc.field("bbox", "ymin") < north)
        & (pc.field("bbox", "ymax") > south)
    )
    if extra is not None:
        condition &= extra
    return dataset.to_table(columns=[*columns, "bbox", "geometry"], filter=condition)


def _centre(row) -> tuple[float, float]:
    box = row["bbox"]
    return (box["xmin"] + box["xmax"]) / 2, (box["ymin"] + box["ymax"]) / 2


def fetch_population(bbox, out: Path) -> None:
    import rasterio
    from rasterio.windows import from_bounds

    with rasterio.open(HRSL_VRT) as source:
        window = from_bounds(*bbox, source.transform)
        data = source.read(1, window=window, boundless=True, fill_value=0)
        profile = {
            "driver": "GTiff",
            "height": data.shape[0],
            "width": data.shape[1],
            "count": 1,
            "dtype": "float32",
            "crs": source.crs,
            "transform": source.window_transform(window),
            "nodata": 0,
            "compress": "deflate",
        }
    data[~(data > 0)] = 0
    with rasterio.open(out / "population.tif", "w", **profile) as target:
        target.write(data.astype("float32"), 1)
    print(f"population: {float(data.sum()):,.0f} residents", file=sys.stderr)


def fetch_places(bbox, out: Path) -> None:
    import pyarrow.compute as pc

    places = _overture(
        "places", "place", ["names", "basic_category", "taxonomy", "confidence"], bbox
    ).to_pylist()
    jobs: dict[tuple[float, float], float] = defaultdict(float)
    facilities = []
    for row in places:
        if (row.get("confidence") or 0) < 0.5:
            continue
        lon, lat = _centre(row)
        category = row.get("basic_category") or ""
        hierarchy = (row.get("taxonomy") or {}).get("hierarchy") or []
        jobs[(round(lon, 4), round(lat, 4))] += JOBS_PER_PLACE.get(category, 4)
        kind = next(
            (FACILITY_CATEGORIES[c] for c in [category, *hierarchy] if c in FACILITY_CATEGORIES),
            None,
        )
        if kind:
            name = ((row.get("names") or {}).get("primary") or "").replace("\n", " ")
            facilities.append((kind, name, lon, lat))

    buildings = _overture(
        "buildings",
        "building",
        ["class", "height", "num_floors"],
        bbox,
        pc.field("class").isin(list(FLOOR_PER_JOB)),
    ).to_pylist()
    for row in buildings:
        box = row["bbox"]
        lon, lat = _centre(row)
        width = (box["xmax"] - box["xmin"]) * 111_320 * math.cos(math.radians(lat))
        depth = (box["ymax"] - box["ymin"]) * 110_540
        floors = row.get("num_floors") or max(1, round((row.get("height") or 4) / 3.5))
        # Half the bounding box approximates the footprint of an irregular building.
        floor_area = 0.5 * width * depth * floors
        jobs[(round(lon, 4), round(lat, 4))] += floor_area / FLOOR_PER_JOB[row["class"]]

    with open(out / "jobs.csv", "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["lon", "lat", "jobs"])
        for (lon, lat), value in sorted(jobs.items()):
            writer.writerow([lon, lat, round(value, 1)])
    with open(out / "facilities.csv", "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["kind", "name", "lon", "lat"])
        writer.writerows(facilities)
    print(
        f"jobs: {sum(jobs.values()):,.0f} estimated; facilities: {len(facilities)}",
        file=sys.stderr,
    )


def fetch_turn_restrictions(bbox, out: Path) -> None:
    """Overture road segments' prohibited transitions, as OSM way pairs."""
    import pyarrow.compute as pc

    segments = _overture(
        "transportation",
        "segment",
        ["id", "sources", "prohibited_transitions", "connectors"],
        bbox,
        pc.field("subtype") == "road",
    ).to_pylist()
    from shapely import wkb

    way_of: dict[str, int] = {}
    connector_at: dict[str, tuple[float, float]] = {}
    for row in segments:
        for source in row.get("sources") or []:
            match = re.match(r"w(\d+)", source.get("record_id") or "")
            if match:
                way_of[row["id"]] = int(match.group(1))
                break
        line = wkb.loads(row["geometry"])
        for connector in row.get("connectors") or []:
            point = line.interpolate(float(connector.get("at") or 0.0), normalized=True)
            connector_at[connector["connector_id"]] = (point.x, point.y)

    rows = []
    for row in segments:
        for rule in row.get("prohibited_transitions") or []:
            sequence = rule.get("sequence") or []
            when = rule.get("when") or {}
            # Single-step, unconditional rules only; timed or vehicle-specific ones skipped.
            if len(sequence) != 1 or when.get("during") or when.get("vehicle"):
                continue
            if rule.get("between"):
                continue
            step = sequence[0]
            from_way, to_way = way_of.get(row["id"]), way_of.get(step["segment_id"])
            via = connector_at.get(step["connector_id"])
            if not from_way or not to_way or via is None:
                continue
            rows.append(
                {
                    "id": f"overture-{row['id'][:8]}-{len(rows)}",
                    "from_way": from_way,
                    "to_way": to_way,
                    "via_lon": round(via[0], 7),
                    "via_lat": round(via[1], 7),
                    "kind": "no_turn",
                }
            )
    (out / "turn_restrictions.json").write_text(json.dumps(rows))
    print(f"turn restrictions: {len(rows)}", file=sys.stderr)


def _openaq_keys(location: str, year: int) -> list[str]:
    keys: list[str] = []
    token = None
    while True:
        query = {"list-type": "2", "prefix": f"records/csv.gz/locationid={location}/year={year}/"}
        if token:
            query["continuation-token"] = token
        listing = (
            urllib.request.urlopen(OPENAQ + "?" + urllib.parse.urlencode(query), timeout=60)
            .read()
            .decode()
        )
        keys += re.findall(r"<Key>([^<]+)</Key>", listing)
        match = re.search(r"<NextContinuationToken>([^<]+)<", listing)
        if not match:
            return keys
        token = match.group(1)


def _openaq_no2(key: str) -> list[dict]:
    try:
        raw = urllib.request.urlopen(OPENAQ + key, timeout=60).read()
    except OSError:
        return []
    text = gzip.decompress(raw).decode()
    return [r for r in csv.DictReader(io.StringIO(text)) if r.get("parameter") == "no2"]


def fetch_no2(locations: list[str], year: int, out: Path, min_hours: int = 2_000) -> None:
    """Annual mean NO2 at OpenAQ locations, from the public archive."""
    stations = []
    for location in locations:
        with ThreadPoolExecutor(32) as pool:
            rows = [r for rs in pool.map(_openaq_no2, _openaq_keys(location, year)) for r in rs]
        values = [
            float(r["value"])
            for r in rows
            if r.get("value") and float(r["value"]) >= 0 and r.get("units") in ("µg/m³", "ug/m3")
        ]
        if len(values) < min_hours:
            print(f"no2: {location} has {len(values)} hours, skipped", file=sys.stderr)
            continue
        first = rows[0]
        stations.append(
            (
                # Archive names look like '"Budapest Teleki"-931918'.
                re.sub(r"-\d+$", "", first["location"]).replace('"', "").strip(),
                float(first["lon"]),
                float(first["lat"]),
                sum(values) / len(values),
                len(values),
            )
        )
    # The same monitor can appear under two providers: keep the fuller record.
    kept: list[tuple] = []
    for station in sorted(stations, key=lambda s: -s[4]):
        if all(
            math.hypot((station[1] - k[1]) * 75_000, (station[2] - k[2]) * 111_000) > 300
            for k in kept
        ):
            kept.append(station)
    with open(out / "no2_stations.csv", "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["name", "lon", "lat", "no2_ugm3", "hours", "year"])
        for name, lon, lat, mean, hours in kept:
            writer.writerow([name, lon, lat, round(mean, 2), hours, year])
    print(f"no2 stations: {len(kept)}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bbox", required=True, help="west,south,east,north")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--gtfs-url")
    parser.add_argument("--openaq-locations", default="", help="comma-separated location ids")
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument(
        "--skip", default="", help="comma-separated: population,places,turns,gtfs,no2"
    )
    args = parser.parse_args()
    bbox = tuple(float(v) for v in args.bbox.split(","))
    skip = set(filter(None, args.skip.split(",")))
    args.out.mkdir(parents=True, exist_ok=True)
    if "population" not in skip:
        fetch_population(bbox, args.out)
    if "places" not in skip:
        fetch_places(bbox, args.out)
    if "turns" not in skip:
        fetch_turn_restrictions(bbox, args.out)
    if args.gtfs_url and "gtfs" not in skip:
        urllib.request.urlretrieve(args.gtfs_url, args.out / "gtfs.zip")
    if args.openaq_locations and "no2" not in skip:
        fetch_no2(args.openaq_locations.split(","), args.year, args.out)


if __name__ == "__main__":
    main()
