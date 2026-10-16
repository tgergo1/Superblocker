import networkx as nx
import numpy as np
import pytest

from app.models.schemas import ModificationType, StreetModification
from app.models.simulation import CapacitySimulationSettings
from app.services.simulation.capacity_simulator import CapacitySimulator, _tree_loads

STEP = 0.0009  # about 100 m


def grid_city(size: int = 13, arterial_every: int = 4) -> nx.MultiDiGraph:
    """A grid with secondary roads every few blocks and residential streets between."""
    graph = nx.MultiDiGraph()
    for row in range(size):
        for column in range(size):
            graph.add_node(row * size + column, x=19.0 + column * STEP * 1.48, y=47.5 + row * STEP)
    osm_id = 1
    for row in range(size):
        for column in range(size):
            node = row * size + column
            for d_row, d_column in ((0, 1), (1, 0)):
                other_row, other_column = row + d_row, column + d_column
                if other_row >= size or other_column >= size:
                    continue
                other = other_row * size + other_column
                line = row if d_row == 0 else column
                highway = "secondary" if line % arterial_every == 0 else "residential"
                for u, v in ((node, other), (other, node)):
                    graph.add_edge(u, v, key=0, osmid=osm_id, highway=highway, length=100.0)
                osm_id += 1
    return graph


def superblock_filters(graph: nx.MultiDiGraph, size: int = 13, every: int = 4):
    """Filter interior streets where they cross the middle of each cell."""
    modifications = []
    for u, v, data in graph.edges(data=True):
        if data["highway"] != "residential" or u > v:
            continue
        row_u, column_u = divmod(u, size)
        row_v, column_v = divmod(v, size)
        crossing_column = row_u == row_v and {column_u % every, column_v % every} == {1, 2}
        crossing_row = column_u == column_v and {row_u % every, row_v % every} == {1, 2}
        if crossing_column or crossing_row:
            modifications.append(
                StreetModification(
                    u=u,
                    v=v,
                    osm_id=data["osmid"],
                    modification_type=ModificationType.MODAL_FILTER,
                )
            )
    return modifications


def simulate(**overrides):
    graph = grid_city()
    options = {"zone_size_m": 250, "demand_levels": [0.5, 1.0, 2.0, 3.0, 4.0], **overrides}
    settings = CapacitySimulationSettings(**options)
    return CapacitySimulator(
        graph, modifications=superblock_filters(graph), settings=settings
    ).run()


def test_tree_loads_push_destination_trips_to_the_origin():
    # Tree from origin 0: 0 -> 1 -> 2, 1 -> 3.
    predecessors = np.array([[-9999, 0, 1, 1]])
    loads = np.array([[0.0, 0.0, 5.0, 2.0]])
    child, parent, carried = _tree_loads(predecessors, loads)
    by_edge = {
        (int(p), int(c)): float(value) for c, p, value in zip(child, parent, carried, strict=True)
    }
    assert by_edge == {(0, 1): 7.0, (1, 2): 5.0, (1, 3): 2.0}


def test_simulation_compares_both_networks_across_demand_levels():
    result = simulate()

    assert result.zone_count >= 4
    assert result.links_closed_by_plan > 0
    assert result.demand_calibrated
    for scenario in (result.baseline, result.planned):
        assert [level.demand_multiplier for level in scenario.levels] == [0.5, 1.0, 2.0, 3.0, 4.0]
        indexes = [level.travel_time_index for level in scenario.levels]
        assert indexes == sorted(indexes)
        assert all(level.blocked_trips == pytest.approx(0.0) for level in scenario.levels)
    # Same trips on both networks; the plan removes shortcuts, so trips are not shorter.
    base_one = result.baseline.levels[1]
    plan_one = result.planned.levels[1]
    assert plan_one.trips == pytest.approx(base_one.trips)
    assert plan_one.vehicle_km >= base_one.vehicle_km * 0.999
    assert result.planned.drivable_km < result.baseline.drivable_km
    assert result.links
    assert any(link.closed_in_plan for link in result.links)


def test_calibration_hits_the_target_peak_travel_time_index():
    light = simulate(demand_levels=[1.0], target_peak_travel_time_index=1.2)
    heavy = simulate(demand_levels=[1.0], target_peak_travel_time_index=1.5)
    assert heavy.base_peak_hour_trips > light.base_peak_hour_trips > 0
    assert light.baseline.levels[0].travel_time_index == pytest.approx(1.2, abs=0.05)
    assert heavy.baseline.levels[0].travel_time_index == pytest.approx(1.5, abs=0.05)


def test_junctions_the_plan_cuts_off_carry_no_trips():
    graph = grid_city(9)
    # A dead-end spur off a residential street, closed by a filter on its only section.
    graph.add_node(999, x=19.0 + 2 * STEP * 1.48, y=47.5 - STEP)
    graph.add_edge(2, 999, key=0, osmid=9_999, highway="residential", length=100.0)
    graph.add_edge(999, 2, key=0, osmid=9_999, highway="residential", length=100.0)
    closure = StreetModification(
        u=2, v=999, osm_id=9_999, modification_type=ModificationType.MODAL_FILTER
    )
    settings = CapacitySimulationSettings(zone_size_m=150, demand_levels=[1.0])
    result = CapacitySimulator(graph, modifications=[closure], settings=settings).run()
    assert result.junctions_cut_off_by_plan == 1
    assert result.planned.levels[0].blocked_trips == pytest.approx(0.0)
    assert any("junctions have no car route" in note for note in result.limitations)


def test_explicit_trip_total_skips_calibration_and_capacity_is_found():
    result = simulate(peak_hour_trips=40_000)
    assert not result.demand_calibrated
    assert result.base_peak_hour_trips == 40_000
    assert result.baseline.capacity_reached
    assert result.baseline.capacity_multiplier is not None
    assert result.capacity_change_percent is not None


async def _bypass_guard():
    yield


@pytest.fixture
def api_client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.api.routes import analysis, simulation
    from app.core.workload import guard_expensive_request
    from app.main import app

    async def fake_graph(*_args, **_kwargs):
        return grid_city(9)

    monkeypatch.setattr(simulation, "get_street_network_graph", fake_graph)
    app.dependency_overrides[guard_expensive_request] = _bypass_guard
    analysis.partition_store._entries.clear()
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    analysis.partition_store._entries.clear()


def _partition_payload():
    from app.models.schemas import BoundingBox, CityPartition

    return CityPartition(
        superblocks=[],
        arterial_network=[],
        bbox=BoundingBox(north=47.51, south=47.5, east=19.02, west=19.0),
        total_area_hectares=0,
        coverage_percent=0,
        total_superblocks=0,
        total_modal_filters=0,
        total_one_way_conversions=0,
        total_street_cuts=0,
        total_unreachable_addresses=0,
    ).model_dump(mode="json")


def test_capacity_endpoints_return_the_comparison(api_client):
    payload = {
        "partition": _partition_payload(),
        "settings": {"zone_size_m": 250, "demand_levels": [1.0, 2.0]},
    }
    response = api_client.post("/api/v1/simulation/capacity", json=payload)
    assert response.status_code == 200
    body = response.json()
    # A plan with no street changes leaves the network as it is.
    assert body["links_closed_by_plan"] == 0
    assert body["vehicle_km_change_percent"] == pytest.approx(0.0, abs=1e-6)

    response = api_client.post("/api/v1/simulation/capacity/stream", json=payload)
    assert response.status_code == 200
    assert '"type": "progress"' in response.text
    assert '"type": "complete"' in response.text

    payload["settings"]["demand_levels"] = [-1]
    assert api_client.post("/api/v1/simulation/capacity", json=payload).status_code == 422
