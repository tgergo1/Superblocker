import pytest
from shapely.geometry import box

from app.services.network.osm_extract import graph_from_extract, road_filter

# A small grid: an east-west primary road, a residential street, a footway
# (not drivable), a private driveway, and a road far outside the area.
OSM_XML = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
  <node id="1" version="1" lat="47.5000" lon="19.0500"/>
  <node id="2" version="1" lat="47.5000" lon="19.0520"/>
  <node id="3" version="1" lat="47.5000" lon="19.0540"/>
  <node id="4" version="1" lat="47.5020" lon="19.0520"/>
  <node id="5" version="1" lat="47.5020" lon="19.0540"/>
  <node id="6" version="1" lat="47.5040" lon="19.0520"/>
  <node id="7" version="1" lat="47.6000" lon="19.2000"/>
  <node id="8" version="1" lat="47.6000" lon="19.2020"/>
  <way id="100" version="1">
    <nd ref="1"/><nd ref="2"/><nd ref="3"/>
    <tag k="highway" v="primary"/><tag k="name" v="Test út"/>
  </way>
  <way id="200" version="1">
    <nd ref="2"/><nd ref="4"/>
    <tag k="highway" v="residential"/><tag k="maxspeed" v="30"/>
  </way>
  <way id="300" version="1">
    <nd ref="4"/><nd ref="5"/>
    <tag k="highway" v="footway"/>
  </way>
  <way id="400" version="1">
    <nd ref="4"/><nd ref="6"/>
    <tag k="highway" v="residential"/><tag k="access" v="private"/>
  </way>
  <way id="500" version="1">
    <nd ref="7"/><nd ref="8"/>
    <tag k="highway" v="primary"/>
  </way>
</osm>
"""
BBOX = (19.049, 47.499, 19.056, 47.505)


@pytest.fixture
def extract(tmp_path):
    path = tmp_path / "budapest.osm"
    path.write_text(OSM_XML, encoding="utf-8")
    return path


def osm_ids(graph):
    ids = set()
    for _u, _v, data in graph.edges(data=True):
        value = data["osmid"]
        ids.update(value if isinstance(value, list) else [value])
    return ids


def test_drive_network_keeps_only_drivable_public_roads_in_the_area(extract):
    graph = graph_from_extract(extract, BBOX, "drive")

    assert osm_ids(graph) == {100, 200}
    assert graph.graph["crs"]
    assert all("x" in data and "y" in data for _node, data in graph.nodes(data=True))


def test_all_network_keeps_footways_and_private_ways_like_osmnx(extract):
    ids = osm_ids(graph_from_extract(extract, BBOX, "all"))
    assert {300, 400} <= ids


def test_polygon_clips_the_graph(extract):
    # Only the primary road reaches into this polygon.
    graph = graph_from_extract(extract, BBOX, "drive", box(19.049, 47.499, 19.051, 47.501))
    assert osm_ids(graph) == {100}


def test_unsupported_network_and_empty_area_are_errors(extract):
    with pytest.raises(ValueError, match="walk"):
        road_filter("walk")
    with pytest.raises(ValueError, match="no roads"):
        graph_from_extract(extract, (0.0, 0.0, 0.1, 0.1), "drive")
    with pytest.raises(FileNotFoundError):
        graph_from_extract(extract.parent / "missing.osm.pbf", BBOX)


def test_download_uses_the_extract_when_configured(extract, monkeypatch):
    from app.models.schemas import BoundingBox
    from app.services import osm_service

    monkeypatch.setattr(osm_service.settings, "osm_extract_path", str(extract))
    bbox = BoundingBox(north=BBOX[3], south=BBOX[1], east=BBOX[2], west=BBOX[0])

    graph = osm_service._download_graph(bbox, "drive", None)

    assert osm_ids(graph) == {100, 200}


def test_boundary_beyond_the_request_box_still_gets_its_roads(extract):
    # The request box covers only the west end; the boundary covers the residential street.
    small_box = (19.049, 47.499, 19.0505, 47.5005)
    graph = graph_from_extract(extract, small_box, "drive", box(19.049, 47.499, 19.056, 47.503))
    assert 200 in osm_ids(graph)


def test_network_cache_key_names_the_source(extract, monkeypatch):
    import os

    from app.services import osm_service

    monkeypatch.setattr(osm_service.settings, "osm_extract_path", None)
    assert osm_service._network_source("drive") == "overpass"

    monkeypatch.setattr(osm_service.settings, "osm_extract_path", str(extract))
    first = osm_service._network_source("drive")
    assert first.startswith("extract:")
    assert osm_service._network_source("walk") == "overpass"

    # Replacing the extract changes the key.
    extract.write_text(OSM_XML + "\n", encoding="utf-8")
    os.utime(extract, (1, 1))
    assert osm_service._network_source("drive") != first
