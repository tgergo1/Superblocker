"""Authenticated import APIs for authoritative planning evidence."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError

from app.core.auth import require_admin_key
from app.core.config import get_settings
from app.models.schemas import BoundingBox
from app.services.importers.evidence import (
    import_access_targets,
    import_authoritative_roads,
    import_demand_model,
    import_gtfs_operational_requirements,
    import_traffic_observations,
)
from app.services.importers.telraam import (
    import_telraam_observations,
    match_segments_to_osm,
    read_segment_mapping,
)
from app.services.osm_service import get_street_network_graph

router = APIRouter(prefix="/evidence/import")
settings = get_settings()
AdminActor = Annotated[str, Depends(require_admin_key)]


async def _read_upload(upload: UploadFile, *, binary: bool = False) -> str | bytes:
    payload = await upload.read(settings.registry_max_upload_bytes + 1)
    if len(payload) > settings.registry_max_upload_bytes:
        raise HTTPException(status_code=413, detail="Evidence file exceeds the upload limit")
    if binary:
        return payload
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=422, detail="Evidence file must use UTF-8 encoding"
        ) from exc


def _invalid_evidence(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=f"Invalid evidence input: {exc}")


@router.post("/traffic")
async def import_traffic(
    observations: Annotated[UploadFile, File()],
    _actor_id: AdminActor,
):
    """Convert direction-, lane-, class-, and time-specific count CSV to request JSON."""
    try:
        return {
            "traffic_observations": import_traffic_observations(await _read_upload(observations))
        }
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise _invalid_evidence(exc) from exc


@router.post("/access")
async def import_access(
    access_geojson: Annotated[UploadFile, File()],
    source: Annotated[str, Form(min_length=1, max_length=300)],
    _actor_id: AdminActor,
):
    """Convert surveyed entrance GeoJSON to explicit access targets."""
    try:
        return {
            "access_targets": import_access_targets(
                await _read_upload(access_geojson),
                source=source,
            )
        }
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise _invalid_evidence(exc) from exc


@router.post("/roads")
async def import_roads(
    roads_geojson: Annotated[UploadFile, File()],
    source: Annotated[str, Form(min_length=1, max_length=300)],
    rule_version: Annotated[str, Form(min_length=1, max_length=200)],
    _actor_id: AdminActor,
):
    """Convert a stable-ID road-authority inventory to conflation records."""
    try:
        return {
            "authoritative_road_segments": import_authoritative_roads(
                await _read_upload(roads_geojson),
                source=source,
                rule_version=rule_version,
            )
        }
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise _invalid_evidence(exc) from exc


@router.post("/demand")
async def import_demand(
    zones: Annotated[UploadFile, File()],
    time_slices: Annotated[UploadFile, File()],
    matrix: Annotated[UploadFile, File()],
    model_id: Annotated[str, Form(min_length=1, max_length=200)],
    version: Annotated[str, Form(min_length=1, max_length=200)],
    source: Annotated[str, Form(min_length=1, max_length=300)],
    _actor_id: AdminActor,
    source_reference: Annotated[str | None, Form(max_length=1000)] = None,
):
    """Convert EFM-style zone, period, and class-specific OD tables to a demand model."""
    try:
        return {
            "demand_model": import_demand_model(
                zones_csv=await _read_upload(zones),
                time_slices_csv=await _read_upload(time_slices),
                matrix_csv=await _read_upload(matrix),
                model_id=model_id,
                version=version,
                source=source,
                source_reference=source_reference,
            )
        }
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise _invalid_evidence(exc) from exc


@router.post("/gtfs")
async def import_gtfs(
    gtfs: Annotated[UploadFile, File()],
    source: Annotated[str, Form(min_length=1, max_length=300)],
    _actor_id: AdminActor,
    maximum_detour_percent: Annotated[float, Form(ge=0, le=1000)] = 5.0,
):
    """Convert representative GTFS routes to hard transit-operating requirements."""
    try:
        return {
            "operational_requirements": import_gtfs_operational_requirements(
                await _read_upload(gtfs, binary=True),
                source=source,
                maximum_detour_percent=maximum_detour_percent,
            )
        }
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise _invalid_evidence(exc) from exc


@router.post("/telraam")
async def import_telraam(
    report: Annotated[UploadFile, File()],
    _actor_id: AdminActor,
    segment_mapping: Annotated[UploadFile | None, File()] = None,
    segments_geojson: Annotated[UploadFile | None, File()] = None,
    north: Annotated[float | None, Form()] = None,
    south: Annotated[float | None, Form()] = None,
    east: Annotated[float | None, Form()] = None,
    west: Annotated[float | None, Form()] = None,
    min_uptime: Annotated[float, Form(ge=0, le=1)] = 0.5,
):
    """Convert a Telraam hourly report to peak-hour traffic observations.

    Segments are tied to OSM ways by a ``segment_id,osm_id`` CSV, or matched to
    the street network in the given bounding box from the segments GeoJSON.
    """
    try:
        report_json = json.loads(await _read_upload(report))
        if segment_mapping is not None:
            mapping = read_segment_mapping(await _read_upload(segment_mapping))
        elif segments_geojson is not None and None not in (north, south, east, west):
            graph = await get_street_network_graph(
                BoundingBox(north=north, south=south, east=east, west=west)
            )
            mapping = match_segments_to_osm(await _read_upload(segments_geojson), graph)
        else:
            raise ValueError("send segment_mapping, or segments_geojson with a bounding box")
        observations = import_telraam_observations(report_json, mapping, min_uptime)
        return {
            "traffic_observations": observations,
            "matched_segments": len(mapping),
            "attribution": "Traffic counts: Telraam, CC BY 4.0",
        }
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise _invalid_evidence(exc) from exc
