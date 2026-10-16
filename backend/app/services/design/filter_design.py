"""Turn topology actions into buildable filters that keep buses and blue lights moving.

The constraint solver only knows about cars. Following Ghent's bus gates and
London's emergency-passable filters, this step designs each action:

- a filter or street cut on an OSM bus, trolleybus or tram route becomes a
  camera-enforced bus gate that public transport and emergency vehicles pass;
- other filters become planters with a lockable emergency bollard (or plain
  planters when emergency passage is switched off);
- one-way changes on transit routes are flagged for a contraflow bus lane.

Car topology is unchanged, so every directional validation still holds.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

import networkx as nx

from app.models.schemas import (
    FilterDesignSettings,
    FilterDesignSummary,
    ModificationType,
    OsmContext,
    StreetModification,
    TransportMode,
    VehicleClass,
)

ACTIVE_MODES = [TransportMode.WALK, TransportMode.WHEEL, TransportMode.BICYCLE]
BUS_GATE_CLASSES = [VehicleClass.BUS, VehicleClass.EMERGENCY]


def _edge_osm_ids(graph: nx.MultiDiGraph, modification: StreetModification) -> set[int]:
    ids = {modification.osm_id} if modification.osm_id else set()
    for u, v in ((modification.u, modification.v), (modification.v, modification.u)):
        if not graph.has_edge(u, v):
            continue
        for data in graph[u][v].values():
            value = data.get("osmid", [])
            values = value if isinstance(value, (list, tuple, set)) else [value]
            for item in values:
                try:
                    ids.add(int(item))
                except (TypeError, ValueError):
                    continue
    return ids


def _merge_classes(existing: Iterable[VehicleClass], extra: Iterable[VehicleClass]):
    return sorted(set(existing) | set(extra), key=lambda value: value.value)


@dataclass
class FilterDesigner:
    graph: nx.MultiDiGraph
    context: OsmContext | None
    settings: FilterDesignSettings = field(default_factory=FilterDesignSettings)

    def __post_init__(self) -> None:
        self._refs_by_way: dict[int, set[str]] = defaultdict(set)
        for route in self.context.transit_routes if self.context else []:
            label = route.ref or route.name or f"{route.mode} {route.relation_id}"
            for way_id in route.way_ids:
                self._refs_by_way[way_id].add(f"{route.mode} {label}")
        self.summary = FilterDesignSummary(
            transit_routes_loaded=len(self.context.transit_routes) if self.context else 0,
            transit_ways_loaded=len(self._refs_by_way),
            facilities_loaded=len(self.context.facilities) if self.context else 0,
            limitations=list(self.context.limitations) if self.context else [],
        )
        if self.context is None:
            self.summary.limitations.append(
                "No transit route layer was loaded, so filters on bus routes were not "
                "turned into bus gates."
            )
        self._protected: set[str] = set()

    def route_refs(self, modification: StreetModification) -> list[str]:
        refs: set[str] = set()
        for way_id in _edge_osm_ids(self.graph, modification):
            refs |= self._refs_by_way.get(way_id, set())
        return sorted(refs)

    def design(self, modifications: list[StreetModification]) -> list[StreetModification]:
        return [self._design_one(item) for item in modifications]

    def _design_one(self, modification: StreetModification) -> StreetModification:
        kind = modification.modification_type
        refs = self.route_refs(modification) if self._refs_by_way else []
        if (
            refs
            and self.settings.bus_gates_on_transit_routes
            and kind in {ModificationType.MODAL_FILTER, ModificationType.FULL_CLOSURE}
        ):
            self.summary.bus_gates += 1
            if kind == ModificationType.FULL_CLOSURE:
                self.summary.closures_converted_to_gates += 1
            self._protected.update(refs)
            return modification.model_copy(
                update={
                    "modification_type": ModificationType.MODAL_FILTER,
                    "filter_type": "bus_gate",
                    "transit_route_refs": refs,
                    "allowed_modes": _merge_classes_modes(modification.allowed_modes),
                    "allowed_vehicle_classes": _merge_classes(
                        modification.allowed_vehicle_classes, BUS_GATE_CLASSES
                    ),
                    "emergency_override_required": False,
                    "construction_treatment": (
                        "Camera-enforced bus gate: buses, trolleybuses, trams and "
                        "emergency vehicles pass; general traffic is fined"
                    ),
                    "rationale": (
                        f"{modification.rationale} Bus gate keeps {', '.join(refs[:4])}"
                        f"{' and more' if len(refs) > 4 else ''} on their direct route."
                    ).strip(),
                }
            )
        if kind == ModificationType.MODAL_FILTER:
            if self.settings.emergency_passable_filters:
                self.summary.emergency_bollards += 1
                return modification.model_copy(
                    update={
                        "filter_type": "emergency_bollard",
                        "allowed_vehicle_classes": _merge_classes(
                            modification.allowed_vehicle_classes, [VehicleClass.EMERGENCY]
                        ),
                        "emergency_override_required": True,
                        "construction_treatment": (
                            "Planters with a lockable or retractable bollard that "
                            "emergency services can open"
                        ),
                    }
                )
            self.summary.planters += 1
            return modification.model_copy(update={"filter_type": "planter"})
        if kind == ModificationType.ONE_WAY and refs:
            self.summary.one_way_changes_on_transit += 1
            self._protected.update(refs)
            return modification.model_copy(
                update={
                    "transit_route_refs": refs,
                    "construction_treatment": (
                        "One-way for general traffic with a contraflow bus lane for "
                        + ", ".join(refs[:4])
                    ),
                }
            )
        return modification

    def finish(self) -> FilterDesignSummary:
        self.summary.protected_route_refs = sorted(self._protected)
        return self.summary


def _merge_classes_modes(existing: Iterable[TransportMode]) -> list[TransportMode]:
    return sorted(set(existing) | set(ACTIVE_MODES), key=lambda value: value.value)
