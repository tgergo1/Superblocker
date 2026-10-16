"""
Tests for the superblock constraint enforcer.
"""

import networkx as nx
from shapely.geometry import Polygon

from app.models.schemas import ModificationType, StreetModification
from app.services.constraint.constraint_enforcer import ConstraintEnforcer


def build_graph(highway: str = "secondary") -> nx.MultiDiGraph:
    """Create a simple west-center-east interior graph."""
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=-1.0, y=0.0)
    graph.add_node(2, x=0.0, y=0.0)
    graph.add_node(3, x=1.0, y=0.0)

    for u, v in [(1, 2), (2, 1), (2, 3), (3, 2)]:
        graph.add_edge(
            u,
            v,
            key=0,
            osmid=100 + u + v,
            highway=highway,
            length=40.0,
        )

    return graph


def build_enforcer(graph: nx.MultiDiGraph) -> ConstraintEnforcer:
    """Create an enforcer with west/east entry points."""
    enforcer = ConstraintEnforcer(
        interior_graph=graph,
        boundary_polygon=Polygon([(-2, -2), (2, -2), (2, 2), (-2, 2)]),
        entry_node_ids=[1, 3],
        num_sectors=4,
    )
    enforcer.sectors = enforcer._assign_sectors()
    return enforcer


def test_validate_modifications_respects_one_way_reorientation():
    """Opposing one-way plans should eliminate directed cross-sector travel."""
    enforcer = build_enforcer(build_graph())

    modifications = [
        StreetModification(
            u=1,
            v=2,
            key=0,
            osm_id=103,
            name="West approach",
            modification_type=ModificationType.ONE_WAY,
            direction="u_to_v",
            rationale="Keep traffic flowing inward",
        ),
        StreetModification(
            u=2,
            v=3,
            key=0,
            osm_id=105,
            name="East approach",
            modification_type=ModificationType.ONE_WAY,
            direction="v_to_u",
            rationale="Keep traffic flowing inward",
        ),
    ]

    assert enforcer._validate_modifications(modifications) == []


def test_service_connector_prefers_street_cut():
    """Minor service links should be planned as street cuts."""
    enforcer = build_enforcer(build_graph(highway="service"))

    mod_type, direction = enforcer._determine_modification_type(1, 2, 0, enforcer.graph[1][2][0])

    assert mod_type == "full_closure"
    assert direction is None


def test_secondary_connector_prefers_one_way_reorientation():
    """Higher-order interior connectors should be reoriented, not cut."""
    enforcer = build_enforcer(build_graph(highway="secondary"))

    mod_type, direction = enforcer._determine_modification_type(1, 2, 0, enforcer.graph[1][2][0])

    assert mod_type == "one_way"
    assert direction in {"u_to_v", "v_to_u"}


def test_directional_territory_fallback_blocks_cross_traffic_without_losing_access():
    """The guaranteed fallback keeps every reachable branch assigned to an entry side."""
    graph = nx.MultiDiGraph()
    coordinates = {
        1: (-2.0, 0.0),
        2: (-1.0, 0.0),
        3: (0.0, 0.0),
        4: (1.0, 0.0),
        5: (2.0, 0.0),
        6: (0.0, 1.0),
    }
    for node, (x, y) in coordinates.items():
        graph.add_node(node, x=x, y=y)
    for osmid, (u, v) in enumerate(
        [(1, 2), (2, 3), (3, 4), (4, 5), (3, 6)],
        start=200,
    ):
        for source, target in ((u, v), (v, u)):
            graph.add_edge(
                source,
                target,
                key=0,
                osmid=osmid,
                highway="residential",
                length=40.0,
            )

    enforcer = ConstraintEnforcer(
        interior_graph=graph,
        boundary_polygon=Polygon([(-3, -2), (3, -2), (3, 2), (-3, 2)]),
        entry_node_ids=[1, 5],
        num_sectors=4,
    )
    enforcer.sectors = enforcer._assign_sectors()

    modifications = enforcer._build_directional_territory_modifications()

    assert modifications
    assert all(mod.modification_type != ModificationType.ONE_WAY for mod in modifications)
    assert enforcer._validate_modifications(modifications) == []
    assert enforcer._preserves_entry_access(modifications) is True
    assert enforcer._nodes_reachable_from_entries(
        enforcer._apply_vehicle_modifications(modifications)
    ) == set(graph.nodes)


def test_enforcement_opens_missing_local_direction_for_entry_access():
    """An exit-only local link is automatically made two-way inside its territory."""
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=-1.0, y=0.0)
    graph.add_node(2, x=0.0, y=0.0)
    graph.add_edge(
        2,
        1,
        key=0,
        osmid=300,
        highway="residential",
        length=40.0,
    )
    enforcer = ConstraintEnforcer(
        interior_graph=graph,
        boundary_polygon=Polygon([(-2, -1), (2, -1), (2, 1), (-2, 1)]),
        entry_node_ids=[1],
        num_sectors=4,
    )

    modifications, violations = enforcer.enforce_constraints()

    assert violations == []
    assert [mod.modification_type for mod in modifications] == [ModificationType.TWO_WAY]
    assert enforcer._all_nodes_reachable(modifications) is True


def test_duplicate_reverse_filters_become_one_physical_action():
    modifications = [
        StreetModification(
            u=1,
            v=2,
            key=0,
            osm_id=99,
            name="Example Street",
            modification_type=ModificationType.MODAL_FILTER,
        ),
        StreetModification(
            u=2,
            v=1,
            key=0,
            osm_id=99,
            name="Example Street",
            modification_type=ModificationType.MODAL_FILTER,
        ),
    ]

    deduplicated = ConstraintEnforcer._deduplicate_physical_modifications(modifications)

    assert len(deduplicated) == 1
    assert deduplicated[0].u == 1
    assert deduplicated[0].v == 2


def test_one_way_direction_leaves_graph_untouched_and_is_cached():
    """Direction scoring must not mutate the graph other loops are iterating."""
    enforcer = build_enforcer(build_graph("tertiary"))
    before = sorted(enforcer.graph.edges(keys=True))

    direction = enforcer._compute_optimal_one_way_direction(1, 2)

    assert direction in {"u_to_v", "v_to_u"}
    assert sorted(enforcer.graph.edges(keys=True)) == before
    assert enforcer._direction_cache[(1, 2)] == direction


def test_enforcement_on_tertiary_interior_completes():
    """Higher-hierarchy interiors run the one-way scoring path without raising."""
    enforcer = ConstraintEnforcer(
        interior_graph=build_graph("tertiary"),
        boundary_polygon=Polygon([(-2, -2), (2, -2), (2, 2), (-2, 2)]),
        entry_node_ids=[1, 3],
        num_sectors=4,
    )
    modifications, remaining = enforcer.enforce_constraints()
    assert modifications
    assert remaining == []


def test_compiled_direction_score_matches_networkx_scoring():
    """The SciPy scorer must rank one-way options exactly like the reference."""
    grid = nx.grid_2d_graph(5, 5)
    graph = nx.MultiDiGraph()
    labels = {node: index + 1 for index, node in enumerate(sorted(grid.nodes()))}
    for (x, y), node in labels.items():
        graph.add_node(node, x=float(x), y=float(y))
    for a, b in grid.edges():
        for u, v in ((labels[a], labels[b]), (labels[b], labels[a])):
            graph.add_edge(u, v, key=0, highway="tertiary", length=50.0)
    entries = [labels[(0, 2)], labels[(4, 2)], labels[(2, 0)], labels[(2, 4)]]
    enforcer = ConstraintEnforcer(
        interior_graph=graph,
        boundary_polygon=Polygon([(-1, -1), (5, -1), (5, 5), (-1, 5)]),
        entry_node_ids=entries,
        num_sectors=4,
    )
    enforcer.sectors = enforcer._assign_sectors()
    for u, v in [(labels[(0, 2)], labels[(1, 2)]), (labels[(2, 2)], labels[(2, 3)])]:
        for source, target in ((u, v), (v, u)):
            hidden = [(source, target, 0)]
            reference = enforcer._evaluate_direction_score(
                nx.restricted_view(enforcer.graph, [], hidden)
            )
            assert enforcer._compiled_direction_score((source, target)) == reference


def test_cuts_avoid_streets_kept_open_for_access():
    from app.services.constraint.constraint_enforcer import KEEP_OPEN_ATTRIBUTE

    graph = nx.MultiDiGraph()
    points = {1: (0.0, 0.005), 2: (0.003, 0.005), 3: (0.007, 0.005), 4: (0.01, 0.005)}
    for node, (x, y) in points.items():
        graph.add_node(node, x=x, y=y)
    for u, v in ((1, 2), (2, 3), (3, 4)):
        for a, b in ((u, v), (v, u)):
            graph.add_edge(a, b, 0, highway="residential", length=300.0, osmid=u * 10 + v)
    for a, b in ((1, 2), (2, 1), (2, 3), (3, 2)):
        graph[a][b][0][KEEP_OPEN_ATTRIBUTE] = True
    enforcer = ConstraintEnforcer(
        interior_graph=graph,
        boundary_polygon=Polygon([(0, 0), (0.01, 0), (0.01, 0.01), (0, 0.01)]),
        entry_node_ids=[1, 4],
    )
    modifications, violations = enforcer.enforce_constraints()
    assert not violations
    assert modifications
    assert {(min(m.u, m.v), max(m.u, m.v)) for m in modifications} == {(3, 4)}


def test_a_one_way_trap_does_not_preserve_access():
    graph = nx.MultiDiGraph()
    for node, x in ((1, 0.0), (2, 0.005)):
        graph.add_node(node, x=x, y=0.005)
    for a, b in ((1, 2), (2, 1)):
        graph.add_edge(a, b, 0, highway="residential", length=300.0, osmid=12)
    enforcer = ConstraintEnforcer(
        interior_graph=graph,
        boundary_polygon=Polygon([(0, 0), (0.01, 0), (0.01, 0.01), (0, 0.01)]),
        entry_node_ids=[1],
    )
    # Cars could still drive in to node 2, but never back out.
    trap = StreetModification(
        u=1, v=2, osm_id=12, modification_type=ModificationType.ONE_WAY, direction="u_to_v"
    )
    assert not enforcer._preserves_entry_access([trap])
    assert enforcer._preserves_entry_access([])
