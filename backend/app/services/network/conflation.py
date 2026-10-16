"""Conflate OSM bootstrap edges with a versioned authoritative road inventory."""

from __future__ import annotations

import math
from collections.abc import Iterable

import networkx as nx
import pyproj
from shapely.geometry import LineString, shape
from shapely.ops import transform
from shapely.strtree import STRtree

from app.models.schemas import AuthoritativeRoadSegment, ConflationQualityReport, VehicleClass


def _osm_ids(value) -> list[int]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result = []
    for item in values:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return [item for item in result if item > 0]


def _line_for_edge(graph: nx.MultiDiGraph, u, v, data: dict) -> LineString | None:
    geometry = data.get("geometry")
    if isinstance(geometry, LineString):
        u_data = graph.nodes.get(u, {})
        if {"x", "y"}.issubset(u_data):
            origin = (float(u_data["x"]), float(u_data["y"]))
            first = geometry.coords[0]
            last = geometry.coords[-1]
            first_distance = math.hypot(first[0] - origin[0], first[1] - origin[1])
            last_distance = math.hypot(last[0] - origin[0], last[1] - origin[1])
            if last_distance < first_distance:
                return LineString(reversed(geometry.coords))
        return geometry
    u_data = graph.nodes.get(u, {})
    v_data = graph.nodes.get(v, {})
    if {"x", "y"}.issubset(u_data) and {"x", "y"}.issubset(v_data):
        return LineString([(u_data["x"], u_data["y"]), (v_data["x"], v_data["y"])])
    return None


def _directed_bearing(line: LineString) -> float:
    start = line.coords[0]
    end = line.coords[-1]
    return math.degrees(math.atan2(end[1] - start[1], end[0] - start[0])) % 360


def _bearing(line: LineString) -> float:
    return _directed_bearing(line) % 180


def _bearing_difference(left: LineString, right: LineString) -> float:
    difference = abs(_bearing(left) - _bearing(right))
    return min(difference, 180 - difference)


class NetworkConflator:
    """Match road geometry, retain stable authority IDs, and report coverage."""

    def __init__(self, maximum_match_distance_m: float = 15.0) -> None:
        self.maximum_match_distance_m = maximum_match_distance_m

    def conflate(
        self,
        graph: nx.MultiDiGraph,
        inventory: Iterable[AuthoritativeRoadSegment],
    ) -> tuple[nx.MultiDiGraph, ConflationQualityReport]:
        inventory = list(inventory)
        if not inventory:
            raise ValueError("authoritative road inventory is empty")
        centroid = shape(inventory[0].geometry).centroid
        zone = int((centroid.x + 180) / 6) + 1
        epsg = 32600 + zone if centroid.y >= 0 else 32700 + zone
        projector = pyproj.Transformer.from_crs(
            "EPSG:4326", f"EPSG:{epsg}", always_xy=True
        ).transform
        projected_inventory = [transform(projector, shape(item.geometry)) for item in inventory]
        tree = STRtree(projected_inventory)
        result = graph.copy()
        matched_ids: set[str] = set()
        ambiguous: dict[str, list[str]] = {}
        physical_seen: set[tuple[int, int, int]] = set()
        total_length = 0.0
        matched_length = 0.0
        lane_length = 0.0
        signal_length = 0.0
        unmatched_osm: set[int] = set()
        for u, v, key, data in result.edges(keys=True, data=True):
            physical_key = (min(int(u), int(v)), max(int(u), int(v)), int(key))
            line = _line_for_edge(result, u, v, data)
            if line is None:
                continue
            length = max(0.0, float(data.get("length", 0) or 0))
            count_length = physical_key not in physical_seen
            physical_seen.add(physical_key)
            if count_length:
                total_length += length
            projected = transform(projector, line)
            candidate_indices = tree.query(projected.buffer(self.maximum_match_distance_m))
            candidates = []
            for candidate_index in candidate_indices:
                index = int(candidate_index)
                candidate = projected_inventory[index]
                distance = projected.hausdorff_distance(candidate)
                angle = _bearing_difference(projected, candidate)
                if distance <= self.maximum_match_distance_m * 3 and angle <= 35:
                    candidates.append((distance + angle * 0.25, index))
            candidates.sort()
            if not candidates:
                unmatched_osm.update(_osm_ids(data.get("osmid")))
                continue
            score, best_index = candidates[0]
            best = inventory[best_index]
            if len(candidates) > 1 and candidates[1][0] - score < 2.0:
                ambiguous[f"{u}:{v}:{key}"] = [
                    inventory[index].stable_id for _candidate_score, index in candidates[:5]
                ]
                unmatched_osm.update(_osm_ids(data.get("osmid")))
                continue
            matched_ids.add(best.stable_id)
            if count_length:
                matched_length += length
                if best.lanes_forward is not None or best.lanes_backward is not None:
                    lane_length += length
                if best.signal_controller_id:
                    signal_length += length
            data["authoritative_stable_id"] = best.stable_id
            data["authority_source"] = best.source
            data["traffic_rule_version"] = best.rule_version
            data["road_owner"] = best.owner
            data["road_manager"] = best.road_manager
            data["curb_rule_ids"] = best.curb_rule_ids
            data["signal_controller_id"] = best.signal_controller_id
            data["width_m"] = best.width_m
            data["grade_percent"] = best.grade_percent
            data["max_clearance_m"] = best.clearance_m
            data["permitted_vehicle_classes"] = [
                item.value for item in best.permitted_vehicle_classes
            ]
            if best.capacity_vph is not None:
                data["capacity"] = best.capacity_vph
            if best.speed_limit_kph is not None:
                data["maxspeed"] = best.speed_limit_kph
            directed_difference = abs(
                _directed_bearing(projected) - _directed_bearing(projected_inventory[best_index])
            )
            directed_difference = min(directed_difference, 360 - directed_difference)
            forward = directed_difference <= 90
            directional_lanes = best.lanes_forward if forward else best.lanes_backward
            if directional_lanes is not None:
                data["lanes"] = directional_lanes
            if VehicleClass.ALL_MOTOR not in best.permitted_vehicle_classes:
                data["access"] = "no"
                for vehicle_class in best.permitted_vehicle_classes:
                    tag = {
                        VehicleClass.HEAVY_GOODS: "hgv",
                        VehicleClass.LIGHT_GOODS: "goods",
                        VehicleClass.BUS: "bus",
                        VehicleClass.TAXI: "taxi",
                        VehicleClass.MOTORCYCLE: "motorcycle",
                        VehicleClass.EMERGENCY: "emergency",
                    }.get(vehicle_class, "motor_vehicle")
                    data[tag] = "yes"
        coverage = matched_length / total_length * 100 if total_length else 0.0
        matched_stable_ids = sorted(matched_ids)
        return result, ConflationQualityReport(
            source=inventory[0].source,
            rule_version=inventory[0].rule_version,
            total_graph_road_length_km=total_length / 1_000,
            matched_graph_road_length_km=matched_length / 1_000,
            authoritative_network_coverage_percent=min(100.0, coverage),
            lane_coverage_percent=min(
                100.0, lane_length / total_length * 100 if total_length else 0.0
            ),
            signal_coverage_percent=min(
                100.0,
                signal_length / total_length * 100 if total_length else 0.0,
            ),
            matched_stable_ids=matched_stable_ids,
            unmatched_stable_ids=sorted(
                item.stable_id for item in inventory if item.stable_id not in matched_ids
            ),
            unmatched_osm_ids=sorted(unmatched_osm),
            ambiguous_matches=ambiguous,
        )
