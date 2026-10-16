"""Turn-expanded vehicle graph with restrictions and class permissions."""

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

import networkx as nx

from app.models.schemas import (
    ModificationType,
    StreetModification,
    TurnRestriction,
    VehicleClass,
)

type DirectedEdgeRef = tuple[int, int, int]


def _tag_values(value: Any) -> set[str]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    normalized: set[str] = set()
    for item in values:
        for token in str(item or "").lower().replace("|", ";").split(";"):
            if token.strip():
                normalized.add(token.strip())
    return normalized


def _osm_ids(value: Any) -> set[int]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: set[int] = set()
    for item in values:
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            result.add(parsed)
    return result


def _speed_kph(data: dict[str, Any]) -> float:
    values = data.get("maxspeed")
    if isinstance(values, (list, tuple, set)):
        values = next(iter(values), None)
    try:
        speed = float(str(values).lower().replace("km/h", "").strip())
        if speed > 0:
            return speed
    except (TypeError, ValueError):
        pass
    highway = data.get("highway", "residential")
    if isinstance(highway, (list, tuple, set)):
        highway = next(iter(highway), "residential")
    return {
        "motorway": 90.0,
        "trunk": 70.0,
        "primary": 50.0,
        "secondary": 50.0,
        "tertiary": 40.0,
        "living_street": 20.0,
        "service": 20.0,
    }.get(str(highway), 30.0)


def _travel_time_seconds(data: dict[str, Any]) -> float:
    length_m = max(0.0, float(data.get("length", 0) or 0))
    return length_m / (_speed_kph(data) / 3.6) if length_m else 0.0


def _permission_keys(vehicle_class: VehicleClass) -> tuple[str, ...]:
    if vehicle_class == VehicleClass.EMERGENCY:
        return ("emergency", "motor_vehicle", "vehicle", "access")
    if vehicle_class == VehicleClass.BUS:
        return ("bus", "psv", "motor_vehicle", "vehicle", "access")
    if vehicle_class == VehicleClass.TAXI:
        return ("taxi", "psv", "motor_vehicle", "vehicle", "access")
    if vehicle_class == VehicleClass.HEAVY_GOODS:
        return ("hgv", "goods", "motor_vehicle", "vehicle", "access")
    if vehicle_class == VehicleClass.LIGHT_GOODS:
        return ("goods", "motor_vehicle", "vehicle", "access")
    if vehicle_class == VehicleClass.MOTORCYCLE:
        return ("motorcycle", "motor_vehicle", "vehicle", "access")
    return ("motor_vehicle", "vehicle", "access")


def edge_allows_vehicle(data: dict[str, Any], vehicle_class: VehicleClass) -> bool:
    """Resolve common OSM access tags for one modeled vehicle class."""
    denied = {"no", "private", "agricultural", "forestry"}
    allowed = {"yes", "designated", "permissive", "destination", "delivery"}
    for key in _permission_keys(vehicle_class):
        if key not in data:
            continue
        values = _tag_values(data.get(key))
        if values & allowed:
            return True
        if values & denied:
            return False
    return True


def _class_matches(
    rule: TurnRestriction,
    vehicle_class: VehicleClass,
) -> bool:
    if vehicle_class in rule.except_classes:
        return False
    return VehicleClass.ALL_MOTOR in rule.applies_to or vehicle_class in rule.applies_to


def _in_force(modification: StreetModification, active_time_window: str | None) -> bool:
    """A timed measure applies only in its own windows.

    With no active window it is modelled as in force, matching how timed turn
    restrictions are treated.
    """
    return (
        not modification.time_windows
        or active_time_window is None
        or active_time_window in modification.time_windows
    )


class TurnGraph:
    """A graph whose nodes are directed road edges and arcs are legal turns."""

    def __init__(
        self,
        graph: nx.DiGraph,
        edge_data: dict[DirectedEdgeRef, dict[str, Any]],
    ) -> None:
        self.graph = graph
        self.edge_data = edge_data

    def restricted_to(self, states: set[DirectedEdgeRef]) -> "TurnGraph":
        """The same turn graph limited to the given road states."""
        return TurnGraph(self.graph.subgraph(states), self.edge_data)

    def outgoing_states(self, node_id: int) -> set[DirectedEdgeRef]:
        return {ref for ref in self.graph.nodes if ref[0] == node_id}

    def incoming_states(self, node_id: int) -> set[DirectedEdgeRef]:
        return {ref for ref in self.graph.nodes if ref[1] == node_id}

    def sector_violations(
        self,
        entry_points_by_sector: dict[int, list[int]],
    ) -> list[tuple[int, int]]:
        """Return sector pairs connected by at least one legal vehicle path."""
        starts_by_sector = {
            sector: set().union(*(self.outgoing_states(node) for node in nodes)) if nodes else set()
            for sector, nodes in entry_points_by_sector.items()
        }
        targets_by_sector = {
            sector: set().union(*(self.incoming_states(node) for node in nodes)) if nodes else set()
            for sector, nodes in entry_points_by_sector.items()
        }
        violations: list[tuple[int, int]] = []
        for source_sector, starts in starts_by_sector.items():
            reachable: set[DirectedEdgeRef] = set(starts)
            for start in starts:
                if start in self.graph:
                    reachable.update(nx.descendants(self.graph, start))
            for target_sector, targets in targets_by_sector.items():
                if source_sector == target_sector:
                    continue
                if reachable & targets:
                    violations.append((source_sector, target_sector))
        return violations

    def has_path_between_nodes(self, source: int, target: int) -> bool:
        starts = self.outgoing_states(source)
        targets = self.incoming_states(target)
        if not starts or not targets:
            return False
        reachable: set[DirectedEdgeRef] = set(starts)
        for start in starts:
            reachable.update(nx.descendants(self.graph, start))
        return bool(reachable & targets)

    def shortest_path_between_nodes(
        self,
        source: int,
        target: int,
    ) -> tuple[float, list[DirectedEdgeRef]] | None:
        """Return minimum free-flow minutes and directed road states."""
        if source == target:
            return 0.0, []
        starts = self.outgoing_states(source)
        targets = self.incoming_states(target)
        if not starts or not targets:
            return None
        best_cost_seconds = float("inf")
        best_path: list[DirectedEdgeRef] = []
        for start in starts:
            distances, paths = nx.single_source_dijkstra(
                self.graph,
                start,
                weight="travel_time_s",
            )
            initial_seconds = float(self.graph.nodes[start].get("travel_time_s", 0.0))
            for target_state in targets:
                if target_state not in distances:
                    continue
                cost_seconds = initial_seconds + float(distances[target_state])
                if cost_seconds < best_cost_seconds:
                    best_cost_seconds = cost_seconds
                    best_path = list(paths[target_state])
        if best_cost_seconds == float("inf"):
            return None
        return best_cost_seconds / 60.0, best_path


class TurnGraphBuilder:
    """Build legal turn state from a directed road graph and plan changes."""

    def __init__(
        self,
        graph: nx.MultiDiGraph,
        restrictions: Iterable[TurnRestriction] = (),
    ) -> None:
        self.source_graph = graph
        self.restrictions = list(restrictions)

    def build(
        self,
        vehicle_class: VehicleClass = VehicleClass.CAR,
        modifications: Iterable[StreetModification] = (),
        active_time_window: str | None = None,
    ) -> TurnGraph:
        graph = self._prepare_graph(vehicle_class, list(modifications), active_time_window)
        edge_data: dict[DirectedEdgeRef, dict[str, Any]] = {}
        turn_graph = nx.DiGraph()
        outgoing: dict[int, list[DirectedEdgeRef]] = defaultdict(list)

        for u, v, key, data in graph.edges(keys=True, data=True):
            if not edge_allows_vehicle(data, vehicle_class):
                continue
            ref = (int(u), int(v), int(key))
            edge_data[ref] = dict(data)
            outgoing[int(u)].append(ref)
            turn_graph.add_node(
                ref,
                length_m=max(0.0, float(data.get("length", 0) or 0)),
                travel_time_s=_travel_time_seconds(data),
                osm_ids=sorted(_osm_ids(data.get("osmid"))),
            )

        modification_turns = [
            modification
            for modification in modifications
            if modification.modification_type == ModificationType.TURN_RESTRICTION
            and _in_force(modification, active_time_window)
        ]
        for incoming_ref, incoming_data in edge_data.items():
            previous_node, via_node, _incoming_key = incoming_ref
            for outgoing_ref in outgoing.get(via_node, []):
                _outgoing_start, next_node, _outgoing_key = outgoing_ref
                if next_node == previous_node:
                    continue
                outgoing_data = edge_data[outgoing_ref]
                if self._restricted(
                    incoming_ref,
                    incoming_data,
                    outgoing_ref,
                    outgoing_data,
                    vehicle_class,
                    active_time_window,
                ):
                    continue
                if self._modified_turn_blocked(
                    incoming_ref,
                    incoming_data,
                    outgoing_ref,
                    outgoing_data,
                    modification_turns,
                ):
                    continue
                turn_graph.add_edge(
                    incoming_ref,
                    outgoing_ref,
                    weight=max(1.0, float(outgoing_data.get("length", 1) or 1)),
                    travel_time_s=_travel_time_seconds(outgoing_data),
                )

        return TurnGraph(turn_graph, edge_data)

    def _prepare_graph(
        self,
        vehicle_class: VehicleClass,
        modifications: list[StreetModification],
        active_time_window: str | None = None,
    ) -> nx.MultiDiGraph:
        graph = self.source_graph.copy()
        for modification in modifications:
            if not _in_force(modification, active_time_window):
                continue
            if modification.modification_type == ModificationType.TWO_WAY:
                self._add_reverse(graph, modification)
                continue
            if modification.modification_type == ModificationType.TURN_RESTRICTION:
                continue
            if modification.modification_type == ModificationType.MODAL_FILTER:
                if vehicle_class in modification.allowed_vehicle_classes:
                    continue
                self._remove_both_directions(graph, modification)
            elif modification.modification_type == ModificationType.FULL_CLOSURE:
                self._remove_both_directions(graph, modification)
            elif modification.modification_type == ModificationType.ONE_WAY:
                # Contraflow exemptions, such as a bus lane against the one-way.
                if vehicle_class in modification.allowed_vehicle_classes:
                    continue
                if modification.direction == "u_to_v":
                    self._remove_direction(graph, modification.v, modification.u)
                elif modification.direction == "v_to_u":
                    self._remove_direction(graph, modification.u, modification.v)
        return graph

    @staticmethod
    def _remove_direction(graph: nx.MultiDiGraph, u: int, v: int) -> None:
        if not graph.has_edge(u, v):
            return
        for key in list(graph[u][v].keys()):
            graph.remove_edge(u, v, key)

    def _remove_both_directions(
        self,
        graph: nx.MultiDiGraph,
        modification: StreetModification,
    ) -> None:
        self._remove_direction(graph, modification.u, modification.v)
        self._remove_direction(graph, modification.v, modification.u)

    def _add_reverse(
        self,
        graph: nx.MultiDiGraph,
        modification: StreetModification,
    ) -> None:
        if graph.has_edge(modification.v, modification.u):
            return
        if not graph.has_edge(modification.u, modification.v):
            return
        data = graph[modification.u][modification.v].get(modification.key)
        if data is None:
            data = next(iter(graph[modification.u][modification.v].values()), None)
        if data is not None:
            reverse = dict(data)
            # Edge shapes run from the edge's start node to its end node.
            geometry = reverse.get("geometry")
            if geometry is not None and hasattr(geometry, "reverse"):
                reverse["geometry"] = geometry.reverse()
            graph.add_edge(modification.v, modification.u, **reverse)

    def _restricted(
        self,
        incoming_ref: DirectedEdgeRef,
        incoming_data: dict[str, Any],
        outgoing_ref: DirectedEdgeRef,
        outgoing_data: dict[str, Any],
        vehicle_class: VehicleClass,
        active_time_window: str | None,
    ) -> bool:
        incoming_ids = _osm_ids(incoming_data.get("osmid"))
        outgoing_ids = _osm_ids(outgoing_data.get("osmid"))
        via_node = incoming_ref[1]
        matching_from = [
            rule
            for rule in self.restrictions
            if rule.from_osm_id in incoming_ids
            and rule.via_node_id == via_node
            and _class_matches(rule, vehicle_class)
            and (
                not rule.time_windows
                or active_time_window is None
                or active_time_window in rule.time_windows
            )
        ]
        for rule in matching_from:
            target_matches = rule.to_osm_id in outgoing_ids
            if rule.kind == "no_turn" and target_matches:
                return True
            if rule.kind == "only_turn" and not target_matches:
                return True
        return False

    @staticmethod
    def _modified_turn_blocked(
        incoming_ref: DirectedEdgeRef,
        incoming_data: dict[str, Any],
        outgoing_ref: DirectedEdgeRef,
        outgoing_data: dict[str, Any],
        modifications: list[StreetModification],
    ) -> bool:
        incoming_ids = _osm_ids(incoming_data.get("osmid"))
        outgoing_ids = _osm_ids(outgoing_data.get("osmid"))
        for modification in modifications:
            if modification.via_node_id not in {None, incoming_ref[1]}:
                continue
            from_matches = (
                modification.turn_from_osm_id is None
                or modification.turn_from_osm_id in incoming_ids
            )
            to_matches = (
                modification.turn_to_osm_id is None or modification.turn_to_osm_id in outgoing_ids
            )
            if from_matches and to_matches:
                return True
        return False
