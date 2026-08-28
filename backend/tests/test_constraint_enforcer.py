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
