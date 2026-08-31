import asyncio

import pytest

from app.models.schemas import BoundingBox
from app.services.detection import graph as legacy_detection
from app.services.detection import superblock_analyzer as analyzer_module
from app.services.detection.superblock_analyzer import SuperblockAnalyzer
from app.services.sizing.size_optimizer import SizeOptimizer, calculate_optimal_superblock_size
from tests.test_routing_and_partitioning import partition_graph


def prepared_graph():
    graph = partition_graph()
    for _u, _v, _key, data in graph.edges(keys=True, data=True):
        data["centrality"] = 0.1 if data["highway"] == "primary" else 0.01
    return graph


def test_advanced_detection_scores_complete_metric_candidate():
    graph = prepared_graph()
    analyzer = SuperblockAnalyzer(
        min_area=0.5,
        max_area=10,
        boundary_road_types={"primary"},
    )
    candidates = analyzer._detect_cells(graph)
    assert len(candidates) == 1
    assert set(candidates[0].perimeter_roads) == {101, 102, 103, 104}
    assert set(candidates[0].interior_roads) == {201, 202}

    candidate = analyzer._score_candidate(candidates[0], graph)
    analyzer._plan_interventions(candidate, graph)
    assert candidate.score_breakdown is not None
    assert 0 <= candidate.score <= 100
    assert len(candidate.interventions) == len({item.osm_id for item in candidate.interventions})
    assert analyzer._compute_network_stats(graph)["total_length_km"] == pytest.approx(0.3)


def test_full_advanced_analysis_runs_detection_scoring_and_cache(monkeypatch):
    graph = prepared_graph()
    stored = {}

    class TestCache:
        def get(self, _kind, _params):
            return stored.get("result")

        def set(self, _kind, _params, data, **_kwargs):
            stored["result"] = data
            return True

    monkeypatch.setattr(analyzer_module, "get_cache_service", lambda: TestCache())
    monkeypatch.setattr(analyzer_module.ox, "graph_from_bbox", lambda **_kwargs: graph)
    analyzer = SuperblockAnalyzer(
        min_area=0.5,
        max_area=10,
        boundary_road_types={"primary"},
    )
    progress = []
    bbox = BoundingBox(north=0.002, south=0, east=0.002, west=0)
    result = asyncio.run(
        analyzer.analyze(
            bbox,
            lambda stage, percent, message: progress.append((stage, percent, message)),
        )
    )
    assert len(result["candidates"]) >= 1
    assert result["candidates"][0]["score_breakdown"]
    assert progress[-1][0] == "complete"

    cached = asyncio.run(analyzer.analyze(bbox))
    assert cached == result


def test_legacy_detection_uses_metric_geometry_and_full_road_lists(monkeypatch):
    graph = prepared_graph()
    candidates = legacy_detection.detect_superblocks(
        graph,
        min_area_hectares=0.5,
        max_area_hectares=10,
    )
    assert len(candidates) == 1
    assert set(candidates[0].interior_roads) == {201, 202}

    monkeypatch.setattr(legacy_detection.ox, "graph_from_bbox", lambda **_kwargs: graph)
    analyzed = asyncio.run(
        legacy_detection.analyze_area(
            BoundingBox(north=0.002, south=0, east=0.002, west=0),
            min_area=0.5,
            max_area=10,
        )
    )
    assert len(analyzed) == 1


def test_size_optimizer_applies_density_grid_and_walkability_adjustments():
    graph = prepared_graph()
    dense = SizeOptimizer(graph=graph, population_density=30_000, latitude=47)
    dense_result = dense.calculate_optimal_size()
    assert dense_result.optimal_side_m <= 400
    assert dense_result.grid_orientation_deg != 0
    assert "density" in dense_result.rationale
    assert dense.grid_analysis.street_density > 0

    sparse_result = calculate_optimal_superblock_size(
        graph=None,
        population_density=1_000,
        latitude=-35,
    )
    assert sparse_result.optimal_side_m <= 420
    assert sparse_result.min_area_ha < sparse_result.max_area_ha
