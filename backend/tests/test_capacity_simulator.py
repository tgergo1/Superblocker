import networkx as nx
import numpy as np
import pytest

from app.models.schemas import (
    ModificationType,
    StreetModification,
    TurnRestriction,
    VehicleClass,
)
from app.models.simulation import CapacitySimulationSettings
from app.services.importers.population import PopulationGrid
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


def _base_and_plan(result):
    return result.baseline.levels[0], result.planned.levels[0]


def test_camera_gates_stay_open_to_destination_traffic():
    graph = grid_city()
    gates = [
        modification.model_copy(
            update={
                "filter_type": "camera_gate",
                "allowed_vehicle_classes": [VehicleClass.PERMIT, VehicleClass.EMERGENCY],
            }
        )
        for modification in superblock_filters(graph)
    ]
    settings = CapacitySimulationSettings(zone_size_m=250, demand_levels=[1.0])
    walls = CapacitySimulator(
        graph, modifications=superblock_filters(graph), settings=settings
    ).run()
    open_gates = CapacitySimulator(graph, modifications=gates, settings=settings).run()

    assert open_gates.destination_gates > 0 and walls.destination_gates == 0
    assert open_gates.links_closed_by_plan == 0
    gated = [link for link in open_gates.links if link.gated_in_plan]
    assert gated and not any(link.closed_in_plan for link in gated)
    # Through traffic avoids the gates: they carry far less than today.
    assert sum(link.planned_vph for link in gated) < 0.5 * sum(link.baseline_vph for link in gated)
    _base, planned = _base_and_plan(open_gates)
    assert planned.blocked_trips == pytest.approx(0.0)


def test_turn_restrictions_remove_movements():
    graph = grid_city(9)
    centre = 4 * 9 + 4
    rules = []
    for _u, _v, data in graph.in_edges(centre, data=True):
        for _c, _w, out in graph.out_edges(centre, data=True):
            if out["osmid"] != data["osmid"]:
                rules.append(
                    TurnRestriction(
                        restriction_id=f"{data['osmid']}-{out['osmid']}",
                        from_osm_id=data["osmid"],
                        via_node_id=centre,
                        to_osm_id=out["osmid"],
                        kind="no_turn",
                        source="test",
                    )
                )
    settings = CapacitySimulationSettings(zone_size_m=200, demand_levels=[1.0])
    free = CapacitySimulator(graph, settings=settings).run()
    restricted = CapacitySimulator(graph, settings=settings, turn_restrictions=rules).run()
    assert free.turn_restrictions_applied == 0
    assert restricted.turn_restrictions_applied == len(rules)
    assert restricted.movements == free.movements - len(rules)


def test_trips_follow_residents_and_jobs_and_enter_through_gateways():
    graph = grid_city()
    # Primary roads along the edges make gateways.
    for _u, _v, data in graph.edges(data=True):
        if data["highway"] == "secondary":
            data["highway"] = "primary"
    lon = np.array([19.0 + 1 * STEP * 1.48, 19.0 + 11 * STEP * 1.48])
    lat = np.array([47.5 + 1 * STEP, 47.5 + 11 * STEP])
    residents = PopulationGrid(lon=lon[:1], lat=lat[:1], residents=np.array([5_000.0]), source="t")
    jobs = PopulationGrid(lon=lon[1:], lat=lat[1:], residents=np.array([3_000.0]), source="j")
    settings = CapacitySimulationSettings(zone_size_m=250, demand_levels=[1.0])
    result = CapacitySimulator(graph, settings=settings, residents=residents, jobs=jobs).run()
    assert result.demand_source == "residents (t) and jobs (j)"
    assert result.gateway_count > 0
    assert result.external_trip_share == pytest.approx(settings.external_trip_share)


def test_elastic_demand_drops_trips_when_the_plan_slows_them():
    fixed = simulate(demand_levels=[1.0], demand_elasticity=0.0)
    elastic = simulate(demand_levels=[1.0], demand_elasticity=-0.5)
    assert fixed.planned_elastic is None and fixed.trips_not_made_share is None
    assert elastic.planned_elastic is not None
    assert 0.0 < elastic.trips_not_made_share < 0.5
    _base, planned = _base_and_plan(elastic)
    assert elastic.planned_elastic.trips < planned.trips
    assert elastic.planned_elastic.vehicle_km < planned.vehicle_km
    assert elastic.elastic_vehicle_km_change_percent < elastic.vehicle_km_change_percent


def test_external_share_counts_each_trip_once():
    simulator = CapacitySimulator(grid_city(5), settings=CapacitySimulationSettings())
    simulator._production = np.array([0.5, 0.5])
    simulator._attraction = np.array([0.2, 0.8])
    simulator._gateway_weights = np.array([1.0])
    simulator._external_share = 0.15
    minutes = np.array([[0.0, 5.0, 3.0], [5.0, 0.0, 4.0], [3.0, 4.0, 0.0]])
    shares = simulator._gravity_shares(minutes)
    assert shares.sum() == pytest.approx(1.0)
    external = shares[2, :].sum() + shares[:, 2].sum()
    assert external == pytest.approx(0.15)
    assert shares[2, 2] == 0.0


def test_simulation_models_air_and_noise_with_stations_and_residents():
    from app.services.importers.open_data import AirQualityStation

    graph = grid_city()
    lon = 19.0 + np.arange(1, 12, 2) * STEP * 1.48
    lat = 47.5 + np.arange(1, 12, 2) * STEP
    mesh_lon, mesh_lat = np.meshgrid(lon, lat)
    residents = PopulationGrid(
        lon=mesh_lon.ravel(), lat=mesh_lat.ravel(), residents=np.full(mesh_lon.size, 100.0)
    )
    stations = [
        AirQualityStation("a", lon[0], lat[0], 20.0),
        AirQualityStation("b", lon[2], lat[2], 26.0),
        AirQualityStation("c", lon[4], lat[3], 23.0),
        AirQualityStation("d", lon[5], lat[5], 18.0),
    ]
    settings = CapacitySimulationSettings(zone_size_m=250, demand_levels=[1.0])
    result = CapacitySimulator(
        graph,
        modifications=superblock_filters(graph),
        settings=settings,
        residents=residents,
        stations=stations,
    ).run()
    environment = result.environment
    assert environment is not None
    assert set(environment.scenarios) == {"today", "plan", "plan_with_response"}
    assert environment.residents == pytest.approx(3600.0)
    assert len(environment.stations) == 4


def _centre_cordon(size: int = 13, inner: tuple[int, int] = (4, 8)):
    """A square zone around the grid's middle cells, a little inside the main roads."""
    low, high = inner
    west = 19.0 + (low - 0.5) * STEP * 1.48
    east = 19.0 + (high + 0.5) * STEP * 1.48
    south = 47.5 + (low - 0.5) * STEP
    north = 47.5 + (high + 0.5) * STEP
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


def test_entry_charge_cuts_entries_and_moves_through_traffic_round_the_zone():
    from app.models.simulation import EntryChargeSettings

    charge = EntryChargeSettings(
        cordon=_centre_cordon(), charge=2_000, value_of_time_per_hour=3_000, ring_band_m=150
    )
    result = simulate(demand_levels=[1.0], entry_charge=charge)
    summary = result.entry_charge
    assert summary is not None
    assert summary.cordon_links > 0 and summary.zones_inside > 0
    assert 0.1 < summary.area_km2 < 1.0
    scenarios = summary.scenarios
    assert {"today", "plan", "charge", "plan_with_charge"} <= set(scenarios)
    assert {"charge_low", "charge_high", "plan_with_charge_low", "plan_with_charge_high"} <= set(
        scenarios
    )
    today, charged = scenarios["today"], scenarios["charge"]
    assert charged.cordon_entries < today.cordon_entries
    assert charged.cordon_entries_change_percent < 0
    assert charged.trips < today.trips
    assert charged.revenue_per_peak_hour > 0 and today.revenue_per_peak_hour == 0
    # A stronger response takes more trips off the road.
    assert scenarios["charge_high"].trips < charged.trips < scenarios["charge_low"].trips
    # Together with the plan, fewer trips than the plan alone and fewer entries.
    assert scenarios["plan_with_charge"].trips < scenarios["plan"].trips
    assert scenarios["plan_with_charge"].cordon_entries < scenarios["plan"].cordon_entries
    links = [link for link in result.links if link.charged_vph is not None]
    assert links and any(link.inside_cordon for link in result.links)


def test_residents_inside_the_zone_do_not_respond_to_the_charge():
    from app.models.simulation import EntryChargeSettings

    graph = grid_city()
    charge = EntryChargeSettings(cordon=_centre_cordon(), charge=2_000)
    simulator = CapacitySimulator(
        graph,
        settings=CapacitySimulationSettings(
            zone_size_m=250, demand_levels=[1.0], entry_charge=charge
        ),
    )
    simulator.run()
    assert simulator._base_costs.shape[0] == simulator._base_costs.shape[1]
    from app.services.simulation.capacity_simulator import _Assignment

    zones = simulator._base_costs.shape[0]
    crossings = np.ones((zones, zones))
    assignment = _Assignment(
        flows=np.zeros(1),
        arc_flows=np.zeros(1),
        times=np.zeros(1),
        served=0.0,
        blocked=0.0,
        gap=0.0,
        iterations=0,
        crossings=crossings,
    )
    network = simulator._network
    factor = simulator._charge_factor(network, assignment, -1.0)
    inside = network.zone_inside
    assert inside.any()
    assert np.all(factor[inside] == 1.0)
    assert np.all(factor[~inside] < 1.0)
