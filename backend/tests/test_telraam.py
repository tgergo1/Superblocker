import json

import networkx as nx
import pytest

from app.services.importers.telraam import (
    TELRAAM_SOURCE,
    import_telraam_observations,
    match_segments_to_osm,
    peak_hours,
    read_segment_mapping,
)

REPORT = {
    "report": [
        # Morning peak; the API has already corrected the counts for uptime.
        {"segment_id": 9000001, "date": "2026-10-13T07:00:00.000Z", "interval": "hourly",
         "uptime": 0.8, "car": 360, "heavy": 40, "bike": 50, "pedestrian": 80},
        # Higher raw count, but the sensor was mostly blind: dropped.
        {"segment_id": 9000001, "date": "2026-10-13T08:00:00.000Z", "interval": "hourly",
         "uptime": 0.2, "car": 900, "heavy": 10},
        {"segment_id": 9000001, "date": "2026-10-13T16:00:00.000Z", "interval": "hourly",
         "uptime": 1.0, "car": 300, "heavy": 20},
        {"segment_id": 9000002, "date": "2026-10-13T07:00:00.000Z", "interval": "hourly",
         "uptime": 0.9, "car": 90, "heavy": 0},
        # A camera that was not counting.
        {"segment_id": 9000003, "date": "2026-10-13T07:00:00.000Z", "interval": "hourly",
         "uptime": 0, "car": 50, "heavy": 0},
    ]
}  # fmt: skip


def test_peak_hour_skips_blind_hours_without_rescaling():
    peaks = peak_hours(REPORT)

    assert peaks[9000001].volume_vph == 400
    assert peaks[9000001].hour_start == "2026-10-13T07:00:00.000Z"
    assert peaks[9000002].volume_vph == 90
    assert 9000003 not in peak_hours(REPORT, min_uptime=0)


def test_segments_on_one_way_give_one_observation_with_the_busiest_peak():
    observations = import_telraam_observations(REPORT, {9000001: 123, 9000002: 123})

    assert len(observations) == 1
    assert observations[0].volume_vph == 400
    assert observations[0].dataset_id == "telraam-segments-9000001,9000002"


def test_observations_carry_source_interval_and_confidence():
    observations = import_telraam_observations(REPORT, {9000001: 123})

    assert len(observations) == 1
    observation = observations[0]
    assert observation.osm_id == 123
    assert observation.volume_vph == 400
    assert observation.source == TELRAAM_SOURCE
    assert observation.interval_end.startswith("2026-10-13T08:00:00")
    assert observation.confidence == pytest.approx(0.8)


def test_segment_mapping_csv():
    assert read_segment_mapping("segment_id,osm_id\n9000001,123\n9000002,\n") == {9000001: 123}


def test_segments_match_the_nearest_street_within_reach():
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=19.05, y=47.50)
    graph.add_node(2, x=19.052, y=47.50)
    graph.add_node(3, x=19.05, y=47.502)
    graph.add_edge(1, 2, 0, osmid=111)
    graph.add_edge(1, 3, 0, osmid=222)
    segments = {
        "type": "FeatureCollection",
        "features": [
            # Runs along the east-west street, a few metres off.
            {"type": "Feature", "properties": {"segment_id": 9000001},
             "geometry": {"type": "LineString",
                          "coordinates": [[19.0505, 47.50003], [19.0515, 47.50003]]}},
            # Far from every street.
            {"type": "Feature", "properties": {"oidn": 9000002},
             "geometry": {"type": "LineString",
                          "coordinates": [[19.06, 47.51], [19.061, 47.51]]}},
        ],
    }  # fmt: skip

    assert match_segments_to_osm(json.dumps(segments), graph) == {9000001: 111}
