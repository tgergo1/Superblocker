"""Open data layers that fill in what an Overpass download lacks.

``scripts/fetch_open_data.py`` downloads them once from public sources and
writes plain files; the paths go in the settings below. Every loader here is
optional and never raises: a missing or broken file leaves the layer empty and
the plan or simulation says so.

- Jobs (``JOBS_PATH``): a ``lon,lat,jobs`` CSV, read like the population grid.
  The fetch script estimates jobs from Overture building floor space by use,
  which is a proxy, not a register of workplaces.
- Facilities (``FACILITIES_PATH``): a ``kind,name,lon,lat`` CSV of fire and
  ambulance stations, schools and kindergartens (Overture places).
- Turn restrictions (``TURN_RESTRICTIONS_PATH``): JSON rows naming the OSM way
  a car comes from, the way it may not (or must) turn into and where the two
  meet; the meeting point is matched to the street graph's junction.
- Transit routes: the GTFS feed's shapes (``GTFS_PATH``) are matched to the
  street ways buses, trolleybuses and trams run on.
- Air quality stations (``AIR_QUALITY_STATIONS_PATH``): a
  ``name,lon,lat,no2_ugm3`` CSV of measured annual NO2 means.
"""

from __future__ import annotations

import csv
import io
import itertools
import json
import logging
import math
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
from scipy.spatial import cKDTree

from app.models.schemas import (
    BoundingBox,
    ContextFacility,
    Coordinates,
    OsmContext,
    TransitRoute,
    TurnRestriction,
)
from app.services.importers.population import PopulationGrid, read_population_csv

logger = logging.getLogger(__name__)

JOBS_SOURCE = "jobs estimated from Overture Maps places and buildings (CDLA, ODbL)"
FACILITY_SOURCE = "Overture Maps places (CDLA-Permissive-2.0)"
TURN_SOURCE = "OpenStreetMap turn restrictions via Overture Maps (ODbL)"
GTFS_ROUTE_SOURCE = "GTFS shapes matched to the street network"
METRES_PER_DEGREE = 111_320.0
FACILITY_KINDS = {"fire_station", "ambulance_station", "school", "kindergarten"}
# GTFS route_type codes for road-running transit.
GTFS_ROAD_MODES = {"0": "tram", "3": "bus", "11": "trolleybus"}


def _settings():
    from app.core.config import get_settings

    return get_settings()


def _local_xy(lon, lat, lat0: float) -> np.ndarray:
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    return np.column_stack(
        [lon * METRES_PER_DEGREE * math.cos(math.radians(lat0)), lat * 110_540.0]
    )


# ----------------------------------------------------------------------- jobs


def read_jobs(path: str | Path, bbox: BoundingBox | None = None) -> PopulationGrid:
    """A ``lon,lat,jobs`` CSV as a grid of workplaces."""
    text = Path(path).read_text(encoding="utf-8")
    rows = text.splitlines()
    if rows:
        # The population reader takes a population or residents column.
        rows[0] = rows[0].replace("jobs", "population")
    grid = read_population_csv("\n".join(rows), source=JOBS_SOURCE)
    if bbox is None:
        return grid
    keep = (
        (grid.lon >= bbox.west)
        & (grid.lon <= bbox.east)
        & (grid.lat >= bbox.south)
        & (grid.lat <= bbox.north)
    )
    return PopulationGrid(grid.lon[keep], grid.lat[keep], grid.residents[keep], grid.source)


def load_configured_jobs(bbox: BoundingBox) -> PopulationGrid | None:
    path = _settings().jobs_path
    if not path:
        return None
    try:
        return read_jobs(path, bbox)
    except Exception as error:  # A broken layer must not block the run.
        logger.warning("Jobs layer unavailable: %s", error)
        return None


# ----------------------------------------------------------------- facilities


def read_facilities(path: str | Path, bbox: BoundingBox | None = None) -> list[ContextFacility]:
    """Facility points from a ``kind,name,lon,lat`` CSV."""
    facilities: list[ContextFacility] = []
    text = Path(path).read_text(encoding="utf-8").lstrip("﻿")
    for number, row in enumerate(csv.DictReader(io.StringIO(text)), start=1):
        kind = (row.get("kind") or "").strip()
        if kind not in FACILITY_KINDS:
            continue
        try:
            lon, lat = float(row["lon"]), float(row["lat"])
        except (KeyError, TypeError, ValueError):
            continue
        if bbox is not None and not (
            bbox.west <= lon <= bbox.east and bbox.south <= lat <= bbox.north
        ):
            continue
        facilities.append(
            ContextFacility(
                osm_type="node",
                # Not OSM objects: negative ids keep them apart from OSM ids.
                osm_id=-number,
                kind=kind,
                name=(row.get("name") or "").strip()[:300] or None,
                coordinates=Coordinates(lat=lat, lon=lon),
            )
        )
    return facilities


# ---------------------------------------------------------- turn restrictions


def read_turn_restriction_rows(path: str | Path) -> list[dict[str, Any]]:
    """Rows with from_way, to_way, via_lon, via_lat and kind (no_turn/only_turn)."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else payload.get("restrictions", [])


def match_turn_restrictions(
    rows: list[dict[str, Any]], graph: nx.MultiDiGraph, tolerance_m: float = 15.0
) -> list[TurnRestriction]:
    """Attach each rule to the junction where both of its ways meet."""
    from app.services.network.turn_graph import _osm_ids

    nodes = [node for node, data in graph.nodes(data=True) if "x" in data and "y" in data]
    if not nodes or not rows:
        return []
    lat0 = float(np.mean([graph.nodes[node]["y"] for node in nodes]))
    tree = cKDTree(
        _local_xy(
            [graph.nodes[node]["x"] for node in nodes],
            [graph.nodes[node]["y"] for node in nodes],
            lat0,
        )
    )
    ways_at: dict[Any, set[int]] = defaultdict(set)
    for u, v, data in graph.edges(data=True):
        ids = _osm_ids(data.get("osmid"))
        ways_at[u] |= ids
        ways_at[v] |= ids

    rules: list[TurnRestriction] = []
    for number, row in enumerate(rows):
        try:
            from_way, to_way = int(row["from_way"]), int(row["to_way"])
            point = _local_xy([float(row["via_lon"])], [float(row["via_lat"])], lat0)[0]
            kind = str(row.get("kind", "no_turn"))
        except (KeyError, TypeError, ValueError):
            continue
        if kind not in {"no_turn", "only_turn"} or from_way <= 0 or to_way <= 0:
            continue
        candidates = tree.query_ball_point(point, tolerance_m)
        candidates.sort(key=lambda i: float(np.hypot(*(tree.data[i] - point))))
        via = next(
            (
                nodes[i]
                for i in candidates
                if from_way in ways_at[nodes[i]] and to_way in ways_at[nodes[i]]
            ),
            None,
        )
        if via is None:
            continue
        rules.append(
            TurnRestriction(
                restriction_id=str(row.get("id") or f"open-data-{number}")[:200],
                from_osm_id=from_way,
                via_node_id=int(via),
                to_osm_id=to_way,
                kind=kind,
                source=TURN_SOURCE,
            )
        )
    return rules


def load_configured_turn_restrictions(graph: nx.MultiDiGraph) -> list[TurnRestriction]:
    path = _settings().turn_restrictions_path
    if not path:
        return []
    try:
        return match_turn_restrictions(read_turn_restriction_rows(path), graph)
    except Exception as error:
        logger.warning("Turn restrictions unavailable: %s", error)
        return []


# ------------------------------------------------------------ transit routes


def _read_csv(archive: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    with archive.open(name) as handle:
        text = io.TextIOWrapper(handle, encoding="utf-8-sig")
        return list(csv.DictReader(text))


def gtfs_transit_routes(
    path: str | Path,
    graph: nx.MultiDiGraph,
    tolerance_m: float = 12.0,
    min_share: float = 0.8,
) -> list[TransitRoute]:
    """Street ways each bus, trolleybus and tram route of a GTFS feed runs on.

    A way belongs to a route when at least ``min_share`` of the points along it
    lie within ``tolerance_m`` of one of the route's shapes.
    """
    from app.services.network.turn_graph import _osm_ids

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "shapes.txt" not in names:
            return []
        routes = {
            row["route_id"]: row
            for row in _read_csv(archive, "routes.txt")
            if (row.get("route_type") or "").strip() in GTFS_ROAD_MODES
        }
        shapes_of: dict[str, set[str]] = defaultdict(set)
        for row in _read_csv(archive, "trips.txt"):
            if row.get("route_id") in routes and row.get("shape_id"):
                shapes_of[row["route_id"]].add(row["shape_id"])
        wanted = {shape for shapes in shapes_of.values() for shape in shapes}
        points: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
        for row in _read_csv(archive, "shapes.txt"):
            if row.get("shape_id") in wanted:
                points[row["shape_id"]].append(
                    (
                        int(row["shape_pt_sequence"]),
                        float(row["shape_pt_lon"]),
                        float(row["shape_pt_lat"]),
                    )
                )

    ys = [data["y"] for _node, data in graph.nodes(data=True) if "y" in data]
    if not ys:
        return []
    lat0 = float(np.mean(ys))
    # Sample every way once: points every 10 m along its geometry.
    way_samples: dict[int, list[np.ndarray]] = defaultdict(list)
    for u, v, data in graph.edges(data=True):
        geometry = data.get("geometry")
        if geometry is not None and hasattr(geometry, "coords"):
            coords = np.array(geometry.coords)
        else:
            coords = np.array(
                [
                    [graph.nodes[u]["x"], graph.nodes[u]["y"]],
                    [graph.nodes[v]["x"], graph.nodes[v]["y"]],
                ]
            )
        samples = _densify(_local_xy(coords[:, 0], coords[:, 1], lat0), 10.0)
        for way in _osm_ids(data.get("osmid")):
            way_samples[way].append(samples)
    way_ids = np.array(list(way_samples))
    stacked = [np.vstack(way_samples[way]) for way in way_ids]
    owner = np.concatenate([np.full(len(block), i) for i, block in enumerate(stacked)])
    all_samples = np.vstack(stacked)
    per_way = np.bincount(owner, minlength=way_ids.size)

    result: list[TransitRoute] = []
    for number, (route_id, route) in enumerate(sorted(routes.items())):
        shape_points = [
            _densify(
                _local_xy(
                    [p[1] for p in sorted(points[shape])],
                    [p[2] for p in sorted(points[shape])],
                    lat0,
                ),
                5.0,
            )
            for shape in shapes_of.get(route_id, ())
            if len(points.get(shape, ())) >= 2
        ]
        if not shape_points:
            continue
        tree = cKDTree(np.vstack(shape_points))
        distance, _ = tree.query(all_samples, distance_upper_bound=tolerance_m)
        near = np.bincount(owner[np.isfinite(distance)], minlength=way_ids.size)
        matched = way_ids[(near >= min_share * per_way) & (per_way > 0)]
        if matched.size == 0:
            continue
        result.append(
            TransitRoute(
                relation_id=-(number + 1),
                mode=GTFS_ROAD_MODES[route["route_type"].strip()],
                ref=(route.get("route_short_name") or "").strip()[:50] or None,
                name=(route.get("route_long_name") or "").strip()[:300] or None,
                way_ids=sorted(int(way) for way in matched)[:20_000],
            )
        )
    return result


def _densify(xy: np.ndarray, step: float) -> np.ndarray:
    if len(xy) < 2:
        return xy
    out = [xy[:1]]
    for a, b in itertools.pairwise(xy):
        length = float(np.hypot(*(b - a)))
        count = max(1, int(length // step))
        fractions = np.arange(1, count + 1)[:, None] / count
        out.append(a + fractions * (b - a))
    return np.vstack(out)


# -------------------------------------------------------------------- context


def enrich_context(
    context: OsmContext | None, graph: nx.MultiDiGraph, bbox: BoundingBox
) -> OsmContext | None:
    """Fill transit routes and facilities from open data when OSM had none."""
    settings = _settings()
    if not (settings.facilities_path or settings.gtfs_path):
        return context
    context = context or OsmContext()
    update: dict[str, Any] = {}
    notes: list[str] = []
    if not context.facilities and settings.facilities_path:
        try:
            update["facilities"] = read_facilities(settings.facilities_path, bbox)
            notes.append(f"Emergency stations and schools come from {FACILITY_SOURCE}.")
        except Exception as error:
            logger.warning("Facilities unavailable: %s", error)
    if not context.transit_routes and settings.gtfs_path:
        try:
            update["transit_routes"] = gtfs_transit_routes(settings.gtfs_path, graph)
            notes.append(f"Bus, trolleybus and tram routes come from {GTFS_ROUTE_SOURCE}.")
        except Exception as error:
            logger.warning("GTFS routes unavailable: %s", error)
    if not update:
        return context
    limitations = [
        note for note in context.limitations if "could not be loaded from OpenStreetMap" not in note
    ]
    return context.model_copy(update={**update, "limitations": [*limitations, *notes]})


# ---------------------------------------------------------------- air quality


@dataclass(frozen=True)
class AirQualityStation:
    name: str
    lon: float
    lat: float
    no2_ugm3: float


def read_air_quality_stations(path: str | Path) -> list[AirQualityStation]:
    text = Path(path).read_text(encoding="utf-8").lstrip("﻿")
    stations = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            stations.append(
                AirQualityStation(
                    name=(row.get("name") or "").strip(),
                    lon=float(row["lon"]),
                    lat=float(row["lat"]),
                    no2_ugm3=float(row["no2_ugm3"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return stations


def load_configured_air_quality_stations() -> list[AirQualityStation]:
    path = _settings().air_quality_stations_path
    if not path:
        return []
    try:
        return read_air_quality_stations(path)
    except Exception as error:
        logger.warning("Air quality stations unavailable: %s", error)
        return []
