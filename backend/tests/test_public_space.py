from app.models.schemas import PublicSpaceSettings
from app.services.design.public_space import PublicSpaceEstimator, carriageway_width_m
from tests.test_boundary_impact import cell_graph, cell_input


def test_width_prefers_tags_then_lanes_then_class():
    assert carriageway_width_m({"width": "7.5 m", "highway": "residential"}) == 7.5
    assert carriageway_width_m({"lanes": "2", "highway": "primary"}) == 6.0
    assert carriageway_width_m({"highway": "living_street"}) == 5.0
    assert carriageway_width_m({"width": "huge", "highway": ["tertiary"]}) == 8.0


def test_interior_streets_are_measured_and_centre_junction_is_a_plaza():
    summary = PublicSpaceEstimator(
        cell_graph(), PublicSpaceSettings(reallocation_share=0.5)
    ).estimate([cell_input([])])
    cell = summary.superblocks[0]
    # Four 60 m residential spokes, counted once per physical street.
    assert cell.calmed_street_km == 0.24
    assert cell.calmed_carriageway_m2 == 240 * 6
    assert cell.reclaimable_m2 == 720
    assert len(cell.plaza_candidates) == 1
    assert cell.plaza_candidates[0].lat == 0.001
    assert summary.plaza_candidates == 1
    assert summary.reclaimable_m2 == 720
    assert summary.limitations
