"""Propose school streets in front of schools and kindergartens.

A school street closes the street outside a school to motor traffic during
drop-off and pick-up, with residents, permit holders and emergency vehicles
exempt. Inside a superblock that is a short, local measure on a street that
already carries no through traffic. On a boundary road a closure would push
traffic back into the superblock, so the frontage gets speed and crossing
treatment instead.

Each school or kindergarten from the OpenStreetMap context layer is snapped to
its nearest street. The result is advisory: it does not change the validated
access plan.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

import networkx as nx
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from app.models.schemas import (
    ContextFacility,
    Coordinates,
    SchoolStreet,
    SchoolStreetSettings,
    SchoolStreetSummary,
)
from app.services.impact.boundary_impact import SuperblockImpactInput

METERS_PER_DEGREE_LAT = 110_540.0
METERS_PER_DEGREE_LON_EQUATOR = 111_320.0


def _first(value: Any) -> Any:
    if isinstance(value, (list, tuple, set)):
        return next(iter(value), None)
    return value


class SchoolStreetPlanner:
    def __init__(
        self,
        graph: nx.MultiDiGraph,
        settings: SchoolStreetSettings | None = None,
        transit_way_ids: set[int] | None = None,
    ):
        self.graph = graph
        self.settings = settings or SchoolStreetSettings()
        self.transit_way_ids = transit_way_ids or set()
        latitudes = [data["y"] for _, data in graph.nodes(data=True) if "y" in data]
        self._lat0 = sum(latitudes) / len(latitudes) if latitudes else 0.0
        self._x_scale = METERS_PER_DEGREE_LON_EQUATOR * math.cos(math.radians(self._lat0))

    def _to_m(self, lon: float, lat: float) -> tuple[float, float]:
        return lon * self._x_scale, lat * METERS_PER_DEGREE_LAT

    def _edge_lonlat(self, u: int, v: int, data: dict[str, Any]) -> list[tuple[float, float]]:
        geometry = data.get("geometry")
        if geometry is not None:
            return [(float(x), float(y)) for x, y in geometry.coords]
        u_data, v_data = self.graph.nodes[u], self.graph.nodes[v]
        return [(u_data["x"], u_data["y"]), (v_data["x"], v_data["y"])]

    def plan(
        self,
        facilities: Iterable[ContextFacility],
        superblocks: Iterable[SuperblockImpactInput],
        boundary_edges: Iterable[tuple[int, int, int]] = (),
    ) -> SchoolStreetSummary:
        schools = [f for f in facilities if f.kind in ("school", "kindergarten")]
        summary = SchoolStreetSummary(settings=self.settings, facilities_loaded=len(schools))
        summary.limitations.append(
            "Schools are matched to the nearest street within "
            f"{self.settings.max_frontage_distance_m:.0f} m; the gate a school actually "
            "uses needs a site check."
        )
        if not schools:
            summary.limitations.append(
                "No school or kindergarten layer was loaded, so no school streets were proposed."
            )
            return summary

        interior_owner: dict[tuple[int, int], str] = {}
        boundary_pairs: set[tuple[int, int]] = set()
        for block in superblocks:
            for u, v, _key in block.interior_edges:
                interior_owner[(min(u, v), max(u, v))] = block.superblock_id
            for u, v, _key in block.boundary_edges:
                boundary_pairs.add((min(u, v), max(u, v)))
        for u, v, _key in boundary_edges:
            boundary_pairs.add((min(u, v), max(u, v)))

        records: list[tuple[int, int, int, dict[str, Any], list[tuple[float, float]]]] = []
        geometries: list[LineString] = []
        seen: set[tuple[int, int, int]] = set()
        for u, v, key, data in self.graph.edges(keys=True, data=True):
            pair_key = (min(u, v), max(u, v), key)
            if pair_key in seen:
                continue
            seen.add(pair_key)
            try:
                lonlat = self._edge_lonlat(u, v, data)
            except KeyError:
                continue
            if len(lonlat) < 2:
                continue
            records.append((u, v, key, data, lonlat))
            geometries.append(LineString([self._to_m(x, y) for x, y in lonlat]))
        if not geometries:
            summary.limitations.append("The street graph has no usable edge geometry.")
            return summary
        tree = STRtree(geometries)

        interior_ways: set[int] = set()
        for facility in schools:
            point = Point(self._to_m(facility.coordinates.lon, facility.coordinates.lat))
            index = tree.nearest(point)
            u, v, key, data, lonlat = records[int(index)]
            distance = float(geometries[int(index)].distance(point))
            entry = SchoolStreet(
                facility_osm_type=facility.osm_type,
                facility_osm_id=facility.osm_id,
                kind=facility.kind,
                name=facility.name,
                coordinates=facility.coordinates,
                setting="unassigned",
                distance_m=round(distance, 1),
            )
            if distance > self.settings.max_frontage_distance_m:
                entry.treatment = "No street within the frontage distance; check access on site."
                summary.unassigned += 1
                summary.schools.append(entry)
                continue

            osm_id = _first(data.get("osmid"))
            entry.osm_id = int(osm_id) if osm_id is not None else None
            entry.street_name = _first(data.get("name"))
            entry.u, entry.v, entry.key = u, v, key
            entry.street_path = [Coordinates(lat=y, lon=x) for x, y in lonlat]
            entry.on_transit_route = entry.osm_id in self.transit_way_ids
            windows = (
                self.settings.kindergarten_time_windows
                if facility.kind == "kindergarten"
                else self.settings.school_time_windows
            )
            pair = (min(u, v), max(u, v))
            street = entry.street_name or "the street outside"
            if pair in interior_owner and pair not in boundary_pairs:
                entry.setting = "interior"
                entry.superblock_id = interior_owner[pair]
                entry.time_windows = list(windows)
                entry.treatment = (
                    f"Timed school street on {street}: closed to motor traffic "
                    f"{', '.join(windows)} with removable barriers or a camera; residents, "
                    "permit holders and emergency vehicles exempt."
                )
                if entry.on_transit_route:
                    entry.treatment += " Buses keep access through the gate."
                if entry.osm_id is not None:
                    interior_ways.add(entry.osm_id)
            elif pair in boundary_pairs:
                entry.setting = "boundary"
                entry.treatment = (
                    f"Main-road school frontage on {street}: 30 km/h, a raised zebra "
                    "crossing and guard railing. Closing a boundary road would push its "
                    "traffic into the superblock, so it stays open."
                )
                summary.boundary_frontages += 1
            else:
                entry.treatment = (
                    f"{street} lies outside the generated superblocks; a stand-alone school "
                    "street needs a local traffic check."
                )
                summary.unassigned += 1
            summary.schools.append(entry)

        summary.interior_school_streets = len(interior_ways) + sum(
            1 for item in summary.schools if item.setting == "interior" and item.osm_id is None
        )
        return summary
