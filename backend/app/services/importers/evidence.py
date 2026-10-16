"""CSV, GeoJSON, and GTFS importers for production evidence schemas."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.models.schemas import (
    AccessTarget,
    AuthoritativeRoadSegment,
    Coordinates,
    DemandMatrixEntry,
    DemandModel,
    DemandTimeSlice,
    DemandZone,
    OperationalRequirement,
    TrafficObservation,
    VehicleClass,
)


def _rows(text: str) -> list[dict[str, str]]:
    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


def _optional_float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    return float(value)


def _optional_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    return int(value)


def import_demand_model(
    *,
    zones_csv: str,
    time_slices_csv: str,
    matrix_csv: str,
    model_id: str,
    version: str,
    source: str,
    source_reference: str | None = None,
) -> DemandModel:
    """Import EFM-style zone/time/OD tables with explicit vehicle classes."""
    zones = [
        DemandZone(
            zone_id=row["zone_id"],
            name=row.get("name") or None,
            centroid=Coordinates(lat=float(row["lat"]), lon=float(row["lon"])),
            connector_node_ids=[
                int(item) for item in row.get("connector_node_ids", "").split(";") if item
            ],
            external=row.get("external", "").lower() in {"1", "true", "yes"},
        )
        for row in _rows(zones_csv)
    ]
    slices = [
        DemandTimeSlice(
            time_slice_id=row["time_slice_id"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            duration_minutes=int(row["duration_minutes"]),
        )
        for row in _rows(time_slices_csv)
    ]
    matrix = [
        DemandMatrixEntry(
            origin_zone_id=row["origin_zone_id"],
            destination_zone_id=row["destination_zone_id"],
            time_slice_id=row["time_slice_id"],
            vehicle_class=VehicleClass(row.get("vehicle_class") or VehicleClass.CAR.value),
            trips=float(row["trips"]),
        )
        for row in _rows(matrix_csv)
    ]
    return DemandModel(
        model_id=model_id,
        version=version,
        source=source,
        source_reference=source_reference,
        zones=zones,
        time_slices=slices,
        matrix=matrix,
    )


def import_traffic_observations(csv_text: str) -> list[TrafficObservation]:
    """Import direction/lane/class/turn/time traffic observations."""
    return [
        TrafficObservation(
            osm_id=int(row["osm_id"]),
            volume_vph=int(float(row["volume_vph"])),
            source=row["source"],
            observed_at=row.get("observed_at") or None,
            observation_id=row.get("observation_id") or None,
            interval_start=row.get("interval_start") or None,
            interval_end=row.get("interval_end") or None,
            direction=row.get("direction") or "both",
            lane=_optional_int(row.get("lane")),
            vehicle_class=VehicleClass(row.get("vehicle_class") or "all_motor"),
            turn_from_osm_id=_optional_int(row.get("turn_from_osm_id")),
            turn_to_osm_id=_optional_int(row.get("turn_to_osm_id")),
            speed_kph=_optional_float(row.get("speed_kph")),
            occupancy_percent=_optional_float(row.get("occupancy_percent")),
            queue_length_m=_optional_float(row.get("queue_length_m")),
            confidence=float(row.get("confidence") or 1),
            dataset_id=row.get("dataset_id") or None,
        )
        for row in _rows(csv_text)
    ]


def import_access_targets(geojson_text: str, *, source: str) -> list[AccessTarget]:
    """Import authoritative entrance points and their service constraints."""
    collection = json.loads(geojson_text)
    if collection.get("type") != "FeatureCollection":
        raise ValueError("access input must be a GeoJSON FeatureCollection")
    targets = []
    for index, feature in enumerate(collection.get("features", [])):
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "Point":
            raise ValueError(f"access feature {index + 1} is not a Point")
        lon, lat = geometry["coordinates"][:2]
        properties = feature.get("properties") or {}
        targets.append(
            AccessTarget(
                id=str(feature.get("id") or properties.get("id") or f"access-{index + 1}"),
                coordinates=Coordinates(lat=lat, lon=lon),
                kind=properties.get("kind", "address"),
                label=properties.get("label"),
                source=source,
                entrance_id=properties.get("entrance_id"),
                permitted_vehicle_classes=[
                    VehicleClass(item)
                    for item in properties.get("permitted_vehicle_classes", ["all_motor"])
                ],
                service_windows=properties.get("service_windows", []),
                minimum_clearance_m=properties.get("minimum_clearance_m"),
                maximum_detour_minutes=properties.get("maximum_detour_minutes"),
                critical=bool(properties.get("critical", False)),
                surveyed_at=properties.get("surveyed_at"),
            )
        )
    return targets


def import_authoritative_roads(
    geojson_text: str, *, source: str, rule_version: str
) -> list[AuthoritativeRoadSegment]:
    """Import a road-authority LineString inventory with stable IDs."""
    collection = json.loads(geojson_text)
    if collection.get("type") != "FeatureCollection":
        raise ValueError("road inventory must be a GeoJSON FeatureCollection")
    segments = []
    for index, feature in enumerate(collection.get("features", [])):
        properties: dict[str, Any] = feature.get("properties") or {}
        stable_id = feature.get("id") or properties.get("stable_id")
        if not stable_id:
            raise ValueError(f"road feature {index + 1} has no stable_id")
        segments.append(
            AuthoritativeRoadSegment(
                stable_id=str(stable_id),
                geometry=feature.get("geometry"),
                source=source,
                rule_version=rule_version,
                name=properties.get("name"),
                lanes_forward=properties.get("lanes_forward"),
                lanes_backward=properties.get("lanes_backward"),
                speed_limit_kph=properties.get("speed_limit_kph"),
                width_m=properties.get("width_m"),
                grade_percent=properties.get("grade_percent"),
                clearance_m=properties.get("clearance_m"),
                capacity_vph=properties.get("capacity_vph"),
                signal_controller_id=properties.get("signal_controller_id"),
                curb_rule_ids=properties.get("curb_rule_ids", []),
                permitted_vehicle_classes=[
                    VehicleClass(item)
                    for item in properties.get("permitted_vehicle_classes", ["all_motor"])
                ],
                owner=properties.get("owner"),
                road_manager=properties.get("road_manager"),
            )
        )
    return segments


def _time_seconds(value: str) -> int:
    hour, minute, second = (int(item) for item in value.split(":"))
    return hour * 3_600 + minute * 60 + second


def import_gtfs_operational_requirements(
    gtfs_zip: str | Path | bytes,
    *,
    source: str,
    maximum_detour_percent: float = 5.0,
) -> list[OperationalRequirement]:
    """Create one representative end-to-end hard route requirement per transit route."""
    archive_source = io.BytesIO(gtfs_zip) if isinstance(gtfs_zip, bytes) else gtfs_zip
    with zipfile.ZipFile(archive_source) as archive:

        def read_table(name: str) -> list[dict[str, str]]:
            try:
                content = archive.read(name).decode("utf-8-sig")
            except KeyError as exc:
                raise ValueError(f"GTFS archive is missing {name}") from exc
            return _rows(content)

        stops = {
            row["stop_id"]: Coordinates(lat=float(row["stop_lat"]), lon=float(row["stop_lon"]))
            for row in read_table("stops.txt")
        }
        trips = read_table("trips.txt")
        stop_times: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in read_table("stop_times.txt"):
            stop_times[row["trip_id"]].append(row)
        representative_trip: dict[str, str] = {}
        for trip in trips:
            representative_trip.setdefault(trip["route_id"], trip["trip_id"])
        requirements = []
        for route_id, trip_id in sorted(representative_trip.items()):
            times = sorted(stop_times.get(trip_id, []), key=lambda row: int(row["stop_sequence"]))
            if len(times) < 2:
                continue
            first, last = times[0], times[-1]
            if first["stop_id"] not in stops or last["stop_id"] not in stops:
                continue
            travel_minutes = max(
                0.1,
                (_time_seconds(last["arrival_time"]) - _time_seconds(first["departure_time"])) / 60,
            )
            requirements.append(
                OperationalRequirement(
                    requirement_id=f"gtfs-route-{route_id}",
                    service_type="transit_operation",
                    vehicle_class=VehicleClass.BUS,
                    origin=stops[first["stop_id"]],
                    destination=stops[last["stop_id"]],
                    maximum_travel_time_minutes=travel_minutes * (1 + maximum_detour_percent / 100),
                    maximum_detour_percent=maximum_detour_percent,
                    source=source,
                    source_reference=f"GTFS route {route_id}, representative trip {trip_id}",
                    hard_constraint=True,
                )
            )
        return requirements
