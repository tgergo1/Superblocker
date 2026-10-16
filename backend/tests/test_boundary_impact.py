import networkx as nx

from app.models.schemas import (
    BoundingBox,
    EvaporationScenario,
    ModificationType,
    StreetModification,
    TrafficImpactSettings,
)
from app.services.impact.boundary_impact import BoundaryImpactEstimator, SuperblockImpactInput
from app.services.partitioning.city_partitioner import CityPartitioner

RING = [(1, 5), (5, 2), (2, 8), (8, 3), (3, 7), (7, 4), (4, 9), (9, 1)]
SPOKES = [(5, 6), (6, 7), (9, 6), (6, 8)]


def cell_graph() -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    coordinates = {
        1: (0.0, 0.0),
        5: (0.001, 0.0),
        2: (0.002, 0.0),
        8: (0.002, 0.001),
        3: (0.002, 0.002),
        7: (0.001, 0.002),
        4: (0.0, 0.002),
        9: (0.0, 0.001),
        6: (0.001, 0.001),
    }
    for node, (x, y) in coordinates.items():
        graph.add_node(node, x=x, y=y)
    for index, (u, v) in enumerate(RING):
        for a, b in ((u, v), (v, u)):
            graph.add_edge(
                a, b, key=0, osmid=100 + index, highway="primary", length=150.0, name="Ring"
            )
    for index, (u, v) in enumerate(SPOKES):
        for a, b in ((u, v), (v, u)):
            graph.add_edge(a, b, key=0, osmid=200 + index, highway="residential", length=60.0)
    return graph


def directed(edges):
    return [(a, b, 0) for u, v in edges for a, b in ((u, v), (v, u))]


def cell_input(modifications) -> SuperblockImpactInput:
    return SuperblockImpactInput(
        superblock_id="sb_test",
        interior_edges=directed(SPOKES),
        boundary_edges=directed(RING),
        entry_sectors={5: 0, 8: 1, 7: 2, 9: 3},
        modifications=modifications,
    )


def closures():
    return [
        StreetModification(
            u=6, v=v, osm_id=200 + i, modification_type=ModificationType.MODAL_FILTER
        )
        for i, v in ((1, 7), (3, 8))
    ] + [
        StreetModification(u=5, v=6, osm_id=200, modification_type=ModificationType.MODAL_FILTER),
        StreetModification(u=9, v=6, osm_id=202, modification_type=ModificationType.MODAL_FILTER),
    ]


def test_filtered_cell_moves_through_traffic_to_boundary_roads():
    estimate = BoundaryImpactEstimator(cell_graph()).estimate([cell_input(closures())])

    cell = estimate.superblocks[0]
    assert cell.entry_pairs_checked == 12
    assert cell.cut_through_pairs > 0
    assert cell.unresolved_pairs == 0
    assert cell.displaced_peak_vph > 0
    assert 0 < cell.interior_traffic_reduction_percent <= 100
    assert cell.mean_detour_seconds > 0
    assert estimate.boundary_roads
    road = estimate.boundary_roads[0]
    assert road.name == "Ring"
    assert road.superblock_ids == ["sb_test"]
    added = road.added_peak_vph
    assert added["pessimistic"] >= added["central"] >= added["optimistic"] > 0
    assert road.percent_increase_central > 0
    assert estimate.maximum_percent_increase_central == road.percent_increase_central
    assert estimate.traffic_basis == "modeled_topology"
    assert (
        estimate.added_boundary_vehicle_km_peak["pessimistic"]
        > estimate.added_boundary_vehicle_km_peak["optimistic"]
    )
    assert estimate.interior_vehicle_km_removed_peak > 0


def test_unmodified_cell_displaces_nothing():
    estimate = BoundaryImpactEstimator(cell_graph()).estimate([cell_input([])])

    assert estimate.superblocks[0].cut_through_pairs > 0
    # Cut-through routes still exist, so nothing is displaced.
    assert estimate.total_displaced_peak_vph == 0
    assert estimate.superblocks[0].interior_traffic_reduction_percent == 0
    assert estimate.boundary_roads == []


def test_measured_volume_and_full_evaporation():
    graph = cell_graph()
    for _u, _v, _key, data in graph.edges(keys=True, data=True):
        data["measured_volume_vph"] = 400 if data["highway"] == "residential" else 900
    settings = TrafficImpactSettings(
        through_traffic_share=1.0,
        scenarios=[EvaporationScenario(name="all_vanish", evaporation_rate=1.0)],
        central_scenario="missing",
    )
    estimate = BoundaryImpactEstimator(graph, settings).estimate([cell_input(closures())])

    assert estimate.traffic_basis == "measured_volume"
    assert estimate.total_displaced_peak_vph > 0
    assert all(road.added_peak_vph == {"all_vanish": 0.0} for road in estimate.boundary_roads)
    assert estimate.roads_over_threshold == 0


def test_partition_reports_traffic_impact():
    from tests.test_routing_and_partitioning import partition_graph

    result = CityPartitioner(
        partition_graph(),
        BoundingBox(north=0.002, south=0, east=0.002, west=0),
        target_size_ha=5,
        min_area_ha=0.5,
        max_area_ha=20,
        arterial_road_types={"primary"},
    ).partition()

    assert result.traffic_impact is not None
    assert len(result.traffic_impact.superblocks) == result.total_superblocks
    assert result.traffic_impact.limitations


def test_zero_through_share_displaces_nothing():
    estimate = BoundaryImpactEstimator(
        cell_graph(), TrafficImpactSettings(through_traffic_share=0.0)
    ).estimate([cell_input(closures())])
    assert estimate.total_displaced_peak_vph == 0
    assert estimate.boundary_roads == []


def test_entry_pair_sample_spreads_over_every_sector():
    from app.services.impact.boundary_impact import sample_entry_pairs

    entries = list(range(40))
    sectors = {node: node // 10 for node in entries}  # four sectors of ten entries
    pairs, total = sample_entry_pairs(entries, sectors, cap=120)

    assert total == 40 * 30
    assert 0 < len(pairs) <= 120
    sources = {a for a, _b in pairs}
    # Each sampled entry is checked against all 30 entries in other sectors.
    assert all(sum(1 for a, _b in pairs if a == source) == 30 for source in sources)
    # The old "first 120 pairs" took entries 0-3 only, all in sector 0.
    assert {sectors[source] for source in sources} == {0, 1, 2, 3}
    assert all(sectors[a] != sectors[b] for a, b in pairs)


def test_entry_pair_sample_keeps_every_side_when_sectors_are_uneven():
    from app.services.impact.boundary_impact import sample_entry_pairs

    entries = list(range(12))
    sectors = {node: 0 if node < 10 else node - 9 for node in entries}  # sizes 10, 1, 1
    pairs, total = sample_entry_pairs(entries, sectors, cap=12)

    assert total == 10 * 2 + 2 * 11
    sources = {a for a, _b in pairs}
    assert {sectors[source] for source in sources} == {0, 1, 2}
    assert all(sectors[a] != sectors[b] for a, b in pairs)


def test_entry_pair_sample_keeps_every_pair_under_the_cap():
    from app.services.impact.boundary_impact import sample_entry_pairs

    pairs, total = sample_entry_pairs([5, 7, 8, 9], {5: 0, 8: 1, 7: 2, 9: 3}, cap=200)
    assert total == len(pairs) == 12


def test_sampled_pairs_are_reported():
    settings = TrafficImpactSettings(max_entry_pairs_per_superblock=6)
    item = cell_input(closures())
    # Two entries per side: the corners join the mid-side entries.
    item.entry_sectors.update({1: 0, 2: 1, 3: 2, 4: 3})
    estimate = BoundaryImpactEstimator(cell_graph(), settings).estimate([item])
    cell = estimate.superblocks[0]
    assert cell.entry_pairs_total == 48
    # One entry per side is always kept, each checked against its 6 destinations.
    assert cell.entry_pairs_checked == 24
    assert any("sample of entries" in text for text in estimate.limitations)
