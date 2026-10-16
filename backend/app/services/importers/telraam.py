"""Turn Telraam citizen traffic counts into measured traffic observations.

Telraam (https://telraam.net) publishes hourly counts of cars, heavy vehicles,
bikes and pedestrians from window-mounted sensors under CC BY 4.0. A sensor
counts only while it can see the street, so each hour carries an ``uptime``
share. The API already corrects counts for downtime, so uptime only filters
out unreliable hours (below ``min_uptime``) and sets the confidence.

Telraam segments are its own road pieces, not OpenStreetMap ways, so they are
matched to the nearest OSM street first. Each segment then yields one
observation: its busiest valid hour of motor traffic, which is the peak-hour
volume the traffic estimate uses. Where several segments fall on one OSM way,
the way keeps the busiest of them.
"""

from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import networkx as nx
from shapely.geometry import LineString, Point, shape
from shapely.strtree import STRtree

from app.models.schemas import TrafficObservation, VehicleClass

TELRAAM_SOURCE = "Telraam (CC BY 4.0)"
DEFAULT_MIN_UPTIME = 0.5
DEFAULT_MAX_MATCH_DISTANCE_M = 25.0
METERS_PER_DEGREE_LAT = 110_540.0
METERS_PER_DEGREE_LON_EQUATOR = 111_320.0


@dataclass(frozen=True)
class TelraamPeakHour:
    segment_id: int
    volume_vph: int
    hour_start: str
    uptime: float


def _segment_id(properties: dict[str, Any]) -> int:
    for key in ("segment_id", "oidn", "id"):
        if properties.get(key) not in (None, ""):
            return int(properties[key])
    raise KeyError("segment_id")


def _records(report: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(report, list):
        return report
    for key in ("report", "features"):
        if isinstance(report.get(key), list):
            items = report[key]
            return [item.get("properties", item) for item in items]
    raise ValueError("Telraam report has no 'report' list")


def peak_hours(
    report: dict[str, Any] | list[dict[str, Any]],
    min_uptime: float = DEFAULT_MIN_UPTIME,
) -> dict[int, TelraamPeakHour]:
    """Busiest valid hour of cars plus heavy vehicles per Telraam segment."""
    peaks: dict[int, TelraamPeakHour] = {}
    for record in _records(report):
        if record.get("interval", "hourly") != "hourly":
            continue
        uptime = float(record.get("uptime") or 0)
        # Zero uptime means the camera was not counting at all.
        if uptime <= 0 or uptime < min_uptime:
            continue
        volume = round(float(record.get("car") or 0) + float(record.get("heavy") or 0))
        segment = int(record["segment_id"])
        current = peaks.get(segment)
        if current is None or volume > current.volume_vph:
            peaks[segment] = TelraamPeakHour(
                segment_id=segment,
                volume_vph=volume,
                hour_start=str(record["date"]),
                uptime=uptime,
            )
    return peaks


def _hour_end(hour_start: str) -> str | None:
    try:
        start = datetime.fromisoformat(hour_start.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (start + timedelta(hours=1)).isoformat()


def import_telraam_observations(
    report: dict[str, Any] | list[dict[str, Any]],
    segment_osm_ids: dict[int, int],
    min_uptime: float = DEFAULT_MIN_UPTIME,
) -> list[TrafficObservation]:
    """One peak-hour observation per OSM way with a matched Telraam segment."""
    by_way: dict[int, list[TelraamPeakHour]] = {}
    for segment, peak in sorted(peak_hours(report, min_uptime).items()):
        osm_id = segment_osm_ids.get(segment)
        if osm_id is not None:
            by_way.setdefault(osm_id, []).append(peak)

    observations: list[TrafficObservation] = []
    for osm_id, peaks in sorted(by_way.items()):
        peak = max(peaks, key=lambda item: (item.volume_vph, -item.segment_id))
        segments = ",".join(str(item.segment_id) for item in peaks)
        observations.append(
            TrafficObservation(
                osm_id=osm_id,
                volume_vph=peak.volume_vph,
                source=TELRAAM_SOURCE,
                observed_at=peak.hour_start,
                observation_id=f"telraam-{peak.segment_id}-{peak.hour_start}",
                interval_start=peak.hour_start,
                interval_end=_hour_end(peak.hour_start),
                direction="both",
                vehicle_class=VehicleClass.ALL_MOTOR,
                confidence=round(min(1.0, peak.uptime), 3),
                dataset_id=f"telraam-segments-{segments}"[:200],
            )
        )
    return observations


def read_segment_mapping(csv_text: str) -> dict[int, int]:
    """Read a ``segment_id,osm_id`` table."""
    return {
        int(row["segment_id"]): int(row["osm_id"])
        for row in csv.DictReader(io.StringIO(csv_text))
        if row.get("segment_id") and row.get("osm_id")
    }


def match_segments_to_osm(
    segments_geojson: str | dict[str, Any],
    graph: nx.MultiDiGraph,
    max_distance_m: float = DEFAULT_MAX_MATCH_DISTANCE_M,
) -> dict[int, int]:
    """Match each Telraam segment to the OSM way nearest its midpoint.

    Segments farther than ``max_distance_m`` from any street are left out
    rather than guessed.
    """
    collection = (
        json.loads(segments_geojson) if isinstance(segments_geojson, str) else segments_geojson
    )
    latitudes = [data["y"] for _node, data in graph.nodes(data=True) if "y" in data]
    if not latitudes:
        return {}
    x_scale = METERS_PER_DEGREE_LON_EQUATOR * math.cos(
        math.radians(sum(latitudes) / len(latitudes))
    )

    def to_m(points) -> list[tuple[float, float]]:
        return [(x * x_scale, y * METERS_PER_DEGREE_LAT) for x, y in points]

    lines: list[LineString] = []
    osm_ids: list[int] = []
    for u, v, data in graph.edges(data=True):
        osmid = data.get("osmid")
        if isinstance(osmid, (list, tuple, set)):
            osmid = next(iter(osmid), None)
        if not osmid:
            continue
        geometry = data.get("geometry")
        if geometry is not None:
            points = list(geometry.coords)
        else:
            u_data, v_data = graph.nodes[u], graph.nodes[v]
            points = [(u_data["x"], u_data["y"]), (v_data["x"], v_data["y"])]
        if len(points) < 2:
            continue
        lines.append(LineString(to_m(points)))
        osm_ids.append(int(osmid))
    if not lines:
        return {}
    tree = STRtree(lines)

    matches: dict[int, int] = {}
    for feature in collection.get("features", []):
        geometry = shape(feature["geometry"])
        if geometry.is_empty:
            continue
        midpoint = (
            geometry.interpolate(0.5, normalized=True)
            if isinstance(geometry, LineString)
            else geometry.representative_point()
        )
        point = Point(to_m([(midpoint.x, midpoint.y)])[0])
        index = int(tree.nearest(point))
        if lines[index].distance(point) <= max_distance_m:
            matches[_segment_id(feature.get("properties") or {})] = osm_ids[index]
    return matches
