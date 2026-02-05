"""
Accessibility Validator for Superblock System.

This module validates that all addresses/nodes within a superblock
remain reachable from the arterial network after constraint enforcement
modifications are applied.

Per user requirement: constraint enforcement is prioritized. If addresses
become unreachable, they are flagged for manual review rather than
relaxing the no-through-traffic constraint.
"""

import networkx as nx
import logging
from typing import Optional
from dataclasses import dataclass
from shapely.geometry import Point

from app.models.schemas import (
    EnforcedSuperblock,
    StreetModification,
    UnreachableAddress,
    Coordinates,
    EntryPoint,
)

logger = logging.getLogger(__name__)


@dataclass
class AccessibilityReport:
    """Report on accessibility within a superblock."""
    total_nodes: int
    reachable_nodes: int
    unreachable_nodes: int
    reachability_percent: float
    emergency_access_ok: bool
    unreachable_addresses: list[UnreachableAddress]
    suggested_fixes: list[str]


class AccessibilityValidator:
    """
    Validates accessibility within superblocks after modifications.

    The validator checks that all interior nodes can be reached from
    at least one entry point. It also checks emergency access requirements.
    """

    # Minimum acceptable reachability (flag warning below this)
    MIN_REACHABILITY_PERCENT = 95.0

    # Emergency access threshold (must reach this many nodes)
    EMERGENCY_ACCESS_MIN_NODES = 0.98  # 98% of nodes

    def __init__(
        self,
        graph: nx.MultiDiGraph,
        superblock: EnforcedSuperblock,
    ):
        """
        Initialize the validator.

        Args:
            graph: NetworkX MultiDiGraph of the interior network
            superblock: The superblock to validate
        """
        self.graph = graph.copy()
        self.superblock = superblock

        # Apply modifications to create the actual modified graph
        self.modified_graph = self._apply_modifications()

    def _apply_modifications(self) -> nx.MultiDiGraph:
        """Apply all modifications to create the actual traffic graph."""
        modified = self.graph.copy()

        for mod in self.superblock.modifications:
            if mod.modification_type.value == "modal_filter":
                # Modal filters block vehicle traffic (both directions)
                if modified.has_edge(mod.u, mod.v):
                    for k in list(modified[mod.u][mod.v].keys()):
                        modified[mod.u][mod.v][k]["vehicle_blocked"] = True
                if modified.has_edge(mod.v, mod.u):
                    for k in list(modified[mod.v][mod.u].keys()):
                        modified[mod.v][mod.u][k]["vehicle_blocked"] = True

            elif mod.modification_type.value == "one_way":
                # One-way removes edges in blocked direction
                if mod.direction == "u_to_v":
                    # Keep u->v, block v->u
                    if modified.has_edge(mod.v, mod.u):
                        edges = list(modified[mod.v][mod.u].keys())
                        for k in edges:
                            modified.remove_edge(mod.v, mod.u, k)
                else:
                    # Keep v->u, block u->v
                    if modified.has_edge(mod.u, mod.v):
                        edges = list(modified[mod.u][mod.v].keys())
                        for k in edges:
                            modified.remove_edge(mod.u, mod.v, k)

            elif mod.modification_type.value == "full_closure":
                # Remove all edges between nodes
                if modified.has_edge(mod.u, mod.v):
                    for k in list(modified[mod.u][mod.v].keys()):
                        modified.remove_edge(mod.u, mod.v, k)
                if modified.has_edge(mod.v, mod.u):
                    for k in list(modified[mod.v][mod.u].keys()):
                        modified.remove_edge(mod.v, mod.u, k)

        return modified

    def validate(self) -> AccessibilityReport:
        """
        Validate accessibility and generate a report.

        Returns:
            AccessibilityReport with findings
        """
        entry_nodes = {ep.node_id for ep in self.superblock.entry_points}

        if not entry_nodes:
            return AccessibilityReport(
                total_nodes=self.modified_graph.number_of_nodes(),
                reachable_nodes=0,
                unreachable_nodes=self.modified_graph.number_of_nodes(),
                reachability_percent=0.0,
                emergency_access_ok=False,
                unreachable_addresses=[],
                suggested_fixes=["No entry points defined"],
            )

        # Find all reachable nodes
        reachable = self._find_reachable_nodes(entry_nodes)

        # Find unreachable nodes
        all_nodes = set(self.modified_graph.nodes)
        unreachable_node_ids = all_nodes - reachable - entry_nodes

        total = len(all_nodes)
        reachable_count = len(reachable) + len(entry_nodes)
        unreachable_count = len(unreachable_node_ids)

        reachability_pct = (reachable_count / total * 100) if total > 0 else 100.0

        # Build unreachable address list
        unreachable_addresses = []
        for node_id in unreachable_node_ids:
            node_data = self.modified_graph.nodes.get(node_id, {})
            nearest_sector = self._find_nearest_entry_sector(node_id)

            unreachable_addresses.append(UnreachableAddress(
                node_id=node_id,
                coordinates=Coordinates(
                    lat=node_data.get("y", 0),
                    lon=node_data.get("x", 0),
                ),
                nearest_entry_sector=nearest_sector,
                reason=self._diagnose_unreachability(node_id, entry_nodes),
            ))

        # Check emergency access
        emergency_ok = reachability_pct >= (self.EMERGENCY_ACCESS_MIN_NODES * 100)

        # Generate suggested fixes
        fixes = self._suggest_fixes(unreachable_node_ids, entry_nodes)

        return AccessibilityReport(
            total_nodes=total,
            reachable_nodes=reachable_count,
            unreachable_nodes=unreachable_count,
            reachability_percent=reachability_pct,
            emergency_access_ok=emergency_ok,
            unreachable_addresses=unreachable_addresses,
            suggested_fixes=fixes,
        )

    def _find_reachable_nodes(self, entry_nodes: set[int]) -> set[int]:
        """Find all nodes reachable from any entry point."""
        reachable = set()

        # Build vehicle-traversable graph (exclude blocked edges)
        vehicle_graph = nx.MultiDiGraph()

        for u, v, k, data in self.modified_graph.edges(keys=True, data=True):
            if not data.get("vehicle_blocked", False):
                if u not in vehicle_graph.nodes:
                    vehicle_graph.add_node(u, **self.modified_graph.nodes[u])
                if v not in vehicle_graph.nodes:
                    vehicle_graph.add_node(v, **self.modified_graph.nodes[v])
                vehicle_graph.add_edge(u, v, key=k, **data)

        for entry in entry_nodes:
            if entry not in vehicle_graph.nodes:
                continue

            try:
                descendants = nx.descendants(vehicle_graph, entry)
                reachable.update(descendants)
            except nx.NetworkXError:
                continue

        return reachable

    def _find_nearest_entry_sector(self, node_id: int) -> int:
        """Find the sector of the nearest entry point to a node."""
        if not self.superblock.entry_points:
            return 0

        node_data = self.modified_graph.nodes.get(node_id, {})
        node_x = node_data.get("x", 0)
        node_y = node_data.get("y", 0)

        best_sector = 0
        best_dist = float("inf")

        for ep in self.superblock.entry_points:
            dx = ep.coordinates.lon - node_x
            dy = ep.coordinates.lat - node_y
            dist = dx*dx + dy*dy

            if dist < best_dist:
                best_dist = dist
                best_sector = ep.sector

        return best_sector

    def _diagnose_unreachability(
        self, node_id: int, entry_nodes: set[int]
    ) -> str:
        """
        Diagnose why a node is unreachable.

        Returns a human-readable reason.
        """
        # Check in original graph
        original_reachable = False
        for entry in entry_nodes:
            if entry in self.graph.nodes and node_id in self.graph.nodes:
                try:
                    if nx.has_path(self.graph, entry, node_id):
                        original_reachable = True
                        break
                except nx.NetworkXError:
                    continue

        if not original_reachable:
            return "Node was already isolated in original graph"

        # Node was reachable before modifications
        # Find which modification caused the disconnection
        blocking_mods = []

        for mod in self.superblock.modifications:
            # Check if this modification is on the path
            for entry in entry_nodes:
                if entry not in self.graph.nodes or node_id not in self.graph.nodes:
                    continue

                try:
                    path = nx.shortest_path(self.graph, entry, node_id)
                    path_edges = set(zip(path[:-1], path[1:]))

                    if (mod.u, mod.v) in path_edges or (mod.v, mod.u) in path_edges:
                        blocking_mods.append(mod)
                        break
                except nx.NetworkXError:
                    continue

        if blocking_mods:
            mod_types = [m.modification_type.value for m in blocking_mods]
            return f"Blocked by modifications: {', '.join(set(mod_types))}"

        return "Disconnected after modifications (complex topology)"

    def _suggest_fixes(
        self, unreachable_nodes: set[int], entry_nodes: set[int]
    ) -> list[str]:
        """
        Suggest fixes for unreachable nodes.

        Note: Per user requirements, we prioritize constraint enforcement
        over accessibility. These are suggestions for manual review.
        """
        fixes = []

        if not unreachable_nodes:
            return fixes

        # Group unreachable nodes by proximity
        clusters = self._cluster_unreachable_nodes(unreachable_nodes)

        if len(clusters) == 1 and len(unreachable_nodes) <= 5:
            fixes.append(
                f"Small isolated area ({len(unreachable_nodes)} nodes). "
                "Consider pedestrian-only access or delivery time windows."
            )

        elif len(clusters) > 1:
            fixes.append(
                f"{len(clusters)} disconnected areas found. "
                "Review superblock boundary placement."
            )

        if len(unreachable_nodes) > 10:
            fixes.append(
                "Large number of unreachable nodes. "
                "Consider splitting superblock into smaller units."
            )

        # Check if a single modification is blocking many nodes
        mod_impact = {}
        for mod in self.superblock.modifications:
            impact = self._estimate_modification_impact(mod, unreachable_nodes, entry_nodes)
            if impact > 0:
                mod_impact[mod] = impact

        if mod_impact:
            worst_mod = max(mod_impact, key=mod_impact.get)
            if mod_impact[worst_mod] > len(unreachable_nodes) * 0.5:
                fixes.append(
                    f"Modification at edge ({worst_mod.u}, {worst_mod.v}) "
                    f"blocks {mod_impact[worst_mod]} nodes. Consider alternative placement."
                )

        return fixes

    def _cluster_unreachable_nodes(
        self, unreachable_nodes: set[int]
    ) -> list[set[int]]:
        """Group unreachable nodes by connectivity in original graph."""
        if not unreachable_nodes:
            return []

        # Build subgraph of unreachable nodes
        subgraph = self.graph.subgraph(unreachable_nodes).copy()

        # Find connected components
        undirected = subgraph.to_undirected()
        components = list(nx.connected_components(undirected))

        return [set(c) for c in components]

    def _estimate_modification_impact(
        self,
        mod: StreetModification,
        unreachable_nodes: set[int],
        entry_nodes: set[int],
    ) -> int:
        """
        Estimate how many unreachable nodes would become reachable
        if this modification were removed.
        """
        # Create graph without this modification
        test_graph = self.modified_graph.copy()

        # "Undo" the modification
        if mod.modification_type.value == "modal_filter":
            # Re-add edges from original
            if self.graph.has_edge(mod.u, mod.v):
                for k, data in self.graph[mod.u][mod.v].items():
                    if not test_graph.has_edge(mod.u, mod.v, k):
                        test_graph.add_edge(mod.u, mod.v, key=k, **data)
            if self.graph.has_edge(mod.v, mod.u):
                for k, data in self.graph[mod.v][mod.u].items():
                    if not test_graph.has_edge(mod.v, mod.u, k):
                        test_graph.add_edge(mod.v, mod.u, key=k, **data)

        elif mod.modification_type.value == "one_way":
            # Re-add blocked direction
            if mod.direction == "u_to_v":
                if self.graph.has_edge(mod.v, mod.u):
                    for k, data in self.graph[mod.v][mod.u].items():
                        test_graph.add_edge(mod.v, mod.u, key=k, **data)
            else:
                if self.graph.has_edge(mod.u, mod.v):
                    for k, data in self.graph[mod.u][mod.v].items():
                        test_graph.add_edge(mod.u, mod.v, key=k, **data)

        # Count newly reachable nodes
        newly_reachable = 0
        for entry in entry_nodes:
            if entry not in test_graph.nodes:
                continue

            try:
                descendants = nx.descendants(test_graph, entry)
                for node in unreachable_nodes:
                    if node in descendants:
                        newly_reachable += 1
            except nx.NetworkXError:
                continue

        return newly_reachable


def validate_superblock_accessibility(
    graph: nx.MultiDiGraph,
    superblock: EnforcedSuperblock,
) -> AccessibilityReport:
    """
    Convenience function to validate a superblock's accessibility.

    Args:
        graph: The interior street network graph
        superblock: The superblock to validate

    Returns:
        AccessibilityReport with findings
    """
    validator = AccessibilityValidator(graph, superblock)
    return validator.validate()
