import json
import zipfile

import networkx as nx

from app.models.schemas import BoundingBox, OsmContext
from app.services.importers import open_data
from app.services.importers.open_data import (
    gtfs_transit_routes,
    match_turn_restrictions,
    read_air_quality_stations,
    read_facilities,
    read_jobs,
)

STEP = 0.0009


def line_graph() -> nx.MultiDiGraph:
    """Two ways meeting at node 1, and a side street at node 1."""
    graph = nx.MultiDiGraph()
    graph.add_node(0, x=19.0, y=47.5)
    graph.add_node(1, x=19.0 + STEP * 1.48, y=47.5)
    graph.add_node(2, x=19.0 + 2 * STEP * 1.48, y=47.5)
    graph.add_node(3, x=19.0 + STEP * 1.48, y=47.5 + STEP)
    for u, v, way in ((0, 1, 10), (1, 2, 11), (1, 3, 12)):
        graph.add_edge(u, v, osmid=way, highway="residential", length=100.0)
        graph.add_edge(v, u, osmid=way, highway="residential", length=100.0)
    return graph


def test_turn_restrictions_attach_to_the_junction_both_ways_share():
    graph = line_graph()
    rows = [
        {
            "from_way": 10,
            "to_way": 12,
            "via_lon": 19.0 + STEP * 1.48 + 0.00005,
            "via_lat": 47.5,
            "kind": "no_turn",
        },
        # The ways never meet: dropped.
        {"from_way": 10, "to_way": 99, "via_lon": 19.0, "via_lat": 47.5, "kind": "no_turn"},
        {"from_way": 11, "to_way": 12, "via_lon": 18.0, "via_lat": 47.0, "kind": "no_turn"},
    ]
    rules = match_turn_restrictions(rows, graph)
    assert [(r.from_osm_id, r.via_node_id, r.to_osm_id) for r in rules] == [(10, 1, 12)]


def test_jobs_facilities_and_stations_read_from_csv(tmp_path):
    jobs = tmp_path / "jobs.csv"
    jobs.write_text("lon,lat,jobs\n19.0,47.5,120\n25.0,47.5,50\n19.1,47.5,0\n")
    grid = read_jobs(jobs, BoundingBox(north=47.6, south=47.4, east=19.2, west=18.9))
    assert grid.residents.tolist() == [120.0]

    facilities = tmp_path / "facilities.csv"
    facilities.write_text(
        "kind,name,lon,lat\nschool,A,19.0,47.5\ncafe,B,19.0,47.5\nfire_station,C,19.01,47.51\n"
    )
    found = read_facilities(facilities)
    assert [(f.kind, f.name) for f in found] == [("school", "A"), ("fire_station", "C")]
    assert all(f.osm_id < 0 for f in found)

    stations = tmp_path / "no2.csv"
    stations.write_text("name,lon,lat,no2_ugm3\nX,19.0,47.5,31.5\nbad,,,\n")
    assert [(s.name, s.no2_ugm3) for s in read_air_quality_stations(stations)] == [("X", 31.5)]


def test_gtfs_shapes_match_the_ways_a_bus_runs_on(tmp_path):
    graph = line_graph()
    feed = tmp_path / "feed.zip"
    with zipfile.ZipFile(feed, "w") as archive:
        archive.writestr("routes.txt", "route_id,route_short_name,route_type\nR,9,3\nM,M2,1\n")
        archive.writestr("trips.txt", "route_id,trip_id,shape_id\nR,t1,S\nM,t2,S\n")
        # Along ways 10 and 11, a few metres off the centre line.
        archive.writestr(
            "shapes.txt",
            "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
            f"S,47.50003,19.0,1\nS,47.50003,{19.0 + 2 * STEP * 1.48},2\n",
        )
    routes = gtfs_transit_routes(feed, graph)
    assert len(routes) == 1
    assert routes[0].mode == "bus" and routes[0].ref == "9"
    assert routes[0].way_ids == [10, 11]


def test_enrich_context_fills_missing_layers(tmp_path, monkeypatch):
    facilities = tmp_path / "facilities.csv"
    facilities.write_text("kind,name,lon,lat\nschool,A,19.0,47.5\n")

    class Settings:
        facilities_path = str(facilities)
        gtfs_path = None

    monkeypatch.setattr(open_data, "_settings", lambda: Settings)
    unavailable = OsmContext(
        limitations=["Transit routes ... could not be loaded from OpenStreetMap; skipped."]
    )
    bbox = BoundingBox(north=47.6, south=47.4, east=19.2, west=18.9)
    context = open_data.enrich_context(unavailable, line_graph(), bbox)
    assert len(context.facilities) == 1
    assert not any("could not be loaded" in note for note in context.limitations)
    assert json.dumps(context.model_dump(mode="json"))
