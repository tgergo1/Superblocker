"""
Tests for the superblock constraint enforcer.
"""

import networkx as nx
from shapely.geometry import Polygon

from app.models.schemas import Coordinates, ModificationType, StreetModification
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

    mod_type, direction = enforcer._determine_modification_type(
        1, 2, 0, enforcer.graph[1][2][0]
    )

    assert mod_type == "full_closure"
    assert direction is None


def test_secondary_connector_prefers_one_way_reorientation():
    """Higher-order interior connectors should be reoriented, not cut."""
    enforcer = build_enforcer(build_graph(highway="secondary"))

    mod_type, direction = enforcer._determine_modification_type(
        1, 2, 0, enforcer.graph[1][2][0]
    )

    assert mod_type == "one_way"
    assert direction in {"u_to_v", "v_to_u"}
