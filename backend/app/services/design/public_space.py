"""Estimate the street space a superblock frees for people.

Barcelona and Vienna turn calmed interior streets and filtered junctions into
plazas, trees and play space; the health benefit comes from that
reallocation rather than from the filters themselves. This module measures
the interior carriageway each superblock calms, applies a configurable
reallocation share, and lists junctions whose every leg is an interior
street as candidate plazas.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

import networkx as nx

from app.models.schemas import (
    Coordinates,
    PublicSpaceSettings,
    PublicSpaceSummary,
    SuperblockPublicSpace,
)
from app.services.impact.boundary_impact import SuperblockImpactInput

DEFAULT_WIDTH_M = {
    "primary": 12.0,
    "secondary": 10.0,
    "tertiary": 8.0,
    "unclassified": 6.0,
    "residential": 6.0,
    "living_street": 5.0,
    "service": 4.0,
}


def _first(value: Any) -> Any:
    if isinstance(value, (list, tuple, set)):
        return next(iter(value), None)
    return value


def carriageway_width_m(data: dict[str, Any]) -> float:
    """Measured width when tagged, else lanes x 3 m, else a class default."""
    for key in ("width", "est_width"):
        try:
            width = float(str(_first(data.get(key))).replace("m", "").strip())
            if 2.0 <= width <= 40.0:
                return width
        except (TypeError, ValueError):
            continue
    try:
        lanes = int(float(str(_first(data.get("lanes"))).split(";")[0]))
        if 1 <= lanes <= 8:
            return lanes * 3.0
    except (TypeError, ValueError):
        pass
    highway = str(_first(data.get("highway")) or "residential").removesuffix("_link")
    return DEFAULT_WIDTH_M.get(highway, 6.0)


class PublicSpaceEstimator:
    def __init__(self, graph: nx.MultiDiGraph, settings: PublicSpaceSettings | None = None):
        self.graph = graph
        self.settings = settings or PublicSpaceSettings()

    def estimate(self, superblocks: Iterable[SuperblockImpactInput]) -> PublicSpaceSummary:
        results = [self._one(item) for item in superblocks]
        return PublicSpaceSummary(
            settings=self.settings,
            calmed_street_km=round(sum(item.calmed_street_km for item in results), 2),
            calmed_carriageway_m2=round(sum(item.calmed_carriageway_m2 for item in results)),
            reclaimable_m2=round(sum(item.reclaimable_m2 for item in results)),
            plaza_candidates=sum(len(item.plaza_candidates) for item in results),
            superblocks=results,
            limitations=[
                "Widths come from OSM width or lane tags, else class defaults; parking and "
                "sidewalk layout need a site survey.",
                f"Reclaimable space assumes {self.settings.reallocation_share:.0%} of calmed "
                "carriageway is reallocated, a planning assumption rather than a design.",
            ],
        )

    def _one(self, item: SuperblockImpactInput) -> SuperblockPublicSpace:
        physical: dict[tuple[int, int], dict[str, Any]] = {}
        for u, v, key in item.interior_edges:
            if not self.graph.has_edge(u, v, key):
                continue
            physical.setdefault((min(u, v), max(u, v)), self.graph[u][v][key])
        length_m = sum(float(data.get("length", 0) or 0) for data in physical.values())
        area = sum(
            float(data.get("length", 0) or 0) * carriageway_width_m(data)
            for data in physical.values()
        )
        entry_nodes = set(item.entry_sectors)
        legs: dict[int, int] = defaultdict(int)
        for u, v in physical:
            legs[u] += 1
            legs[v] += 1
        boundary_nodes = {node for u, v, _key in item.boundary_edges for node in (u, v)}
        plazas = []
        for node, count in sorted(legs.items(), key=lambda pair: (-pair[1], pair[0])):
            if count < 3 or node in entry_nodes or node in boundary_nodes:
                continue
            data = self.graph.nodes.get(node, {})
            if "x" in data and "y" in data:
                plazas.append(Coordinates(lat=float(data["y"]), lon=float(data["x"])))
            if len(plazas) >= self.settings.max_plazas_per_superblock:
                break
        return SuperblockPublicSpace(
            superblock_id=item.superblock_id,
            calmed_street_km=round(length_m / 1000, 3),
            calmed_carriageway_m2=round(area),
            reclaimable_m2=round(area * self.settings.reallocation_share),
            plaza_candidates=plazas,
        )
