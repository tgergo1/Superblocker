"""Load public transport routes, emergency stations and schools from Overpass.

These layers turn a topology-only plan into one that respects how the city
actually runs: filters on bus and tram routes become bus gates, emergency
response is checked from real stations, and school entrances get school
streets. Everything here is OpenStreetMap data (ODbL).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import osmnx as ox

from app.core.config import get_settings
from app.models.schemas import BoundingBox, ContextFacility, Coordinates, OsmContext, TransitRoute
from app.services.cache_service import get_cache_service

logger = logging.getLogger(__name__)
settings = get_settings()

TRANSIT_MODES = ("bus", "trolleybus", "tram", "minibus", "share_taxi")
FACILITY_KINDS = ("fire_station", "ambulance_station", "school", "kindergarten")


def build_overpass_query(bbox: BoundingBox, timeout: int = 120) -> str:
    box = f"{bbox.south},{bbox.west},{bbox.north},{bbox.east}"
    modes = "|".join(TRANSIT_MODES)
    return (
        f"[out:json][timeout:{timeout}];"
        f'relation["type"="route"]["route"~"^({modes})$"]({box});'
        "out body;"
        "("
        f'nwr["amenity"~"^(fire_station|ambulance_station|school|kindergarten)$"]({box});'
        f'nwr["emergency"="ambulance_station"]({box});'
        ");"
        "out center tags;"
    )


def _facility_kind(tags: dict[str, Any]) -> str | None:
    amenity = tags.get("amenity")
    if amenity in FACILITY_KINDS:
        return str(amenity)
    if tags.get("emergency") == "ambulance_station":
        return "ambulance_station"
    return None


def parse_overpass_context(payload: dict[str, Any]) -> OsmContext:
    """Turn an Overpass JSON response into transit routes and facilities."""
    routes: list[TransitRoute] = []
    facilities: list[ContextFacility] = []
    for element in payload.get("elements", []):
        tags = element.get("tags") or {}
        element_type = element.get("type")
        if (
            element_type == "relation"
            and tags.get("route") in TRANSIT_MODES
            and "members" in element
        ):
            way_ids = [
                int(member["ref"])
                for member in element.get("members", [])
                if member.get("type") == "way"
                and member.get("role", "") in {"", "forward", "backward"}
            ]
            if way_ids:
                routes.append(
                    TransitRoute(
                        relation_id=int(element["id"]),
                        mode=tags["route"],
                        ref=(tags.get("ref") or None),
                        name=(tags.get("name") or None),
                        way_ids=way_ids,
                    )
                )
            continue
        kind = _facility_kind(tags)
        if kind is None:
            continue
        lat = element.get("lat", (element.get("center") or {}).get("lat"))
        lon = element.get("lon", (element.get("center") or {}).get("lon"))
        if lat is None or lon is None or element_type not in {"node", "way", "relation"}:
            continue
        facilities.append(
            ContextFacility(
                osm_type=element_type,
                osm_id=int(element["id"]),
                kind=kind,
                name=tags.get("name") or None,
                coordinates=Coordinates(lat=float(lat), lon=float(lon)),
            )
        )
    return OsmContext(transit_routes=routes, facilities=facilities)


def fetch_osm_context(bbox: BoundingBox) -> OsmContext:
    """Fetch the context layers, cached like other OSM downloads; never raises."""
    cache = get_cache_service()
    params = {"bbox": bbox.model_dump(), "version": 1}
    cached = cache.get("osm_context", params)
    if cached is not None:
        return OsmContext.model_validate(cached)
    try:
        response = httpx.post(
            ox.settings.overpass_url.rstrip("/") + "/interpreter",
            data={"data": build_overpass_query(bbox, settings.osm_timeout)},
            headers={"User-Agent": settings.nominatim_user_agent},
            timeout=settings.osm_timeout + 30,
        )
        response.raise_for_status()
        context = parse_overpass_context(response.json())
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("OSM context layers unavailable: %s", error)
        return OsmContext(
            limitations=[
                "Transit routes, emergency stations and schools could not be loaded from "
                "OpenStreetMap; bus gates and facility checks were skipped."
            ]
        )
    cache.set("osm_context", params, context.model_dump(mode="json"))
    return context
