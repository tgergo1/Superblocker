import zipfile

import pytest

from app.models.schemas import BoundingBox, Coordinates, OsmContext, TransitStop
from app.services.context import osm_context
from app.services.context.osm_context import _with_gtfs_stops, parse_overpass_context
from app.services.impact.transit_access import TransitAccessChecker
from app.services.importers.gtfs import parse_gtfs_stops, read_gtfs_stops, stops_in_bbox
from app.services.partitioning.city_partitioner import CityPartitioner
from tests.test_boundary_impact import cell_graph, cell_input
from tests.test_routing_and_partitioning import partition_graph


def stop(stop_id: str, lat: float, lon: float, name: str | None = None) -> TransitStop:
    return TransitStop(
        stop_id=stop_id, mode="bus", name=name, coordinates=Coordinates(lat=lat, lon=lon)
    )


def test_stop_in_the_middle_covers_the_whole_superblock():
    summary = TransitAccessChecker(cell_graph(), [stop("s1", 0.001, 0.001, "Centre")]).check(
        [cell_input([])]
    )
    assert summary.checked is True
    assert summary.stops_used == 1
    block = summary.superblocks[0]
    assert block.covered_share == 1.0
    assert block.worst_walk_m == pytest.approx(30.0)
    assert block.nearest_stop == "Centre"
    assert block.stops_nearby == 1
    assert summary.superblocks_poorly_served == 0


def test_corner_stop_covers_only_the_near_streets():
    # Corner 1 to the middle of the near spokes: 150 m ring + 30 m; far spokes: 240 m.
    summary = TransitAccessChecker(
        cell_graph(), [stop("s1", 0.0, 0.0)], walk_threshold_m=200
    ).check([cell_input([])])
    block = summary.superblocks[0]
    assert block.covered_share == pytest.approx(0.5)
    assert block.worst_walk_m == pytest.approx(240.0)
    assert block.nearest_stop == "bus stop s1"
    assert summary.superblocks_poorly_served == 0

    tighter = TransitAccessChecker(
        cell_graph(), [stop("s1", 0.0, 0.0)], walk_threshold_m=150
    ).check([cell_input([])])
    assert tighter.superblocks[0].covered_share == 0.0
    assert tighter.superblocks_poorly_served == 1
    assert tighter.mean_covered_share == 0.0


def test_stops_away_from_the_network_are_not_checked():
    summary = TransitAccessChecker(cell_graph(), [stop("far", 1.0, 1.0)]).check([cell_input([])])
    assert summary.checked is False
    assert summary.superblocks == []
    assert "No public transport stops" in summary.limitations[0]
    assert TransitAccessChecker(cell_graph(), []).check([cell_input([])]).checked is False


def test_overpass_stops_get_a_mode():
    payload = {
        "elements": [
            {
                "type": "node",
                "id": 1,
                "lat": 47.5,
                "lon": 19.0,
                "tags": {"highway": "bus_stop", "name": "Astoria"},
            },
            {"type": "node", "id": 2, "lat": 47.5, "lon": 19.0, "tags": {"railway": "tram_stop"}},
            {
                "type": "node",
                "id": 3,
                "lat": 47.5,
                "lon": 19.0,
                "tags": {"railway": "subway_entrance"},
            },
            {
                "type": "node",
                "id": 4,
                "lat": 47.5,
                "lon": 19.0,
                "tags": {"railway": "station", "station": "subway"},
            },
            {"type": "node", "id": 5, "lat": 47.5, "lon": 19.0, "tags": {"railway": "halt"}},
            {
                "type": "way",
                "id": 6,
                "center": {"lat": 47.5, "lon": 19.0},
                "tags": {"amenity": "ferry_terminal"},
            },
            {"type": "node", "id": 7, "lat": 47.5, "lon": 19.0, "tags": {"amenity": "cafe"}},
        ]
    }
    context = parse_overpass_context(payload)
    assert [item.mode for item in context.transit_stops] == [
        "bus",
        "tram",
        "subway",
        "subway",
        "rail",
        "ferry",
    ]
    assert context.transit_stops[0].stop_id == "node/1"
    assert context.transit_stops[0].name == "Astoria"
    assert context.stop_source.startswith("OpenStreetMap")
    assert context.facilities == []


STOPS_TXT = (
    "﻿stop_id,stop_name,stop_lat,stop_lon,location_type,vehicle_type\n"
    "F00001,Deák Ferenc tér,47.4973,19.0547,0,1\n"
    "F00002,Astoria,47.4935,19.0603,,3\n"
    "F00003,Station group,47.4935,19.0603,1,\n"
    "F00004,Entrance,47.4936,19.0604,2,401\n"
    "F00005,Broken,,19.06,0,\n"
    "F00006,Zero,0,0,0,\n"
    "F00007,Tram,47.50,19.05,0,900\n"
    "F00008,Unknown,47.50,19.05,0,abc\n"
)


def test_gtfs_stops_keep_boarding_points_with_modes(tmp_path):
    stops = parse_gtfs_stops(STOPS_TXT)
    assert [item.stop_id for item in stops] == ["F00001", "F00002", "F00004", "F00007", "F00008"]
    assert [item.mode for item in stops] == ["subway", "bus", "subway", "tram", "other"]
    assert stops[0].name == "Deák Ferenc tér"

    feed = tmp_path / "budapest_gtfs.zip"
    with zipfile.ZipFile(feed, "w") as archive:
        archive.writestr("stops.txt", STOPS_TXT)
        archive.writestr("routes.txt", "route_id\n")
    assert len(read_gtfs_stops(feed)) == 5
    bare = tmp_path / "stops.txt"
    bare.write_text(STOPS_TXT, encoding="utf-8")
    assert len(read_gtfs_stops(bare)) == 5

    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("routes.txt", "route_id\n")
    with pytest.raises(ValueError):
        read_gtfs_stops(empty)

    box = BoundingBox(north=47.495, south=47.49, east=19.07, west=19.05)
    assert [item.stop_id for item in stops_in_bbox(stops, box, margin_deg=0)] == [
        "F00002",
        "F00004",
    ]


def test_configured_gtfs_feed_replaces_osm_stops(monkeypatch, tmp_path):
    feed = tmp_path / "stops.txt"
    feed.write_text(STOPS_TXT, encoding="utf-8")
    box = BoundingBox(north=47.51, south=47.49, east=19.07, west=19.04)
    context = OsmContext(transit_stops=[stop("node/1", 47.5, 19.05)], stop_source="OSM")

    monkeypatch.setattr(osm_context.settings, "gtfs_path", None)
    assert _with_gtfs_stops(context, box) is context

    monkeypatch.setattr(osm_context.settings, "gtfs_path", str(feed))
    swapped = _with_gtfs_stops(context, box)
    assert len(swapped.transit_stops) == 5
    assert swapped.stop_source.startswith("GTFS")

    monkeypatch.setattr(osm_context.settings, "gtfs_path", str(tmp_path / "missing.zip"))
    kept = _with_gtfs_stops(context, box)
    assert kept.transit_stops == context.transit_stops
    assert "GTFS feed could not be read" in kept.limitations[-1]


def test_partition_reports_transit_access():
    bbox = BoundingBox(north=0.002, south=0, east=0.002, west=0)
    result = CityPartitioner(
        partition_graph(),
        bbox,
        target_size_ha=5,
        min_area_ha=0.5,
        max_area_ha=20,
        arterial_road_types={"primary"},
        osm_context=OsmContext(transit_stops=[stop("s1", 0.001, 0.001)], stop_source="test"),
    ).partition()
    assert result.transit_access is not None
    assert result.transit_access.checked is True
    assert result.transit_access.source == "test"
    assert result.transit_access.superblocks
