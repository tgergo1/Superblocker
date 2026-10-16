"""Check how far superblock residents walk to public transport.

A superblock takes cars off its interior streets, so it works best where
people can walk to a bus, tram or metro stop on the boundary instead. This
check snaps every stop to the street network and measures the walking
distance from each interior street to the nearest stop. Superblocks where
less than half of the interior street length is within the threshold are
flagged as poorly served: they need a stop added or better crossings before
a trial.

Walking distances run on the road graph, which leaves out footpaths and
passages, so they are an upper bound in dense blocks.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import networkx as nx
import numpy as np
from scipy.spatial import cKDTree

from app.models.schemas import (
    SuperblockTransitAccess,
    TransitAccessSummary,
    TransitStop,
)
from app.services.impact.boundary_impact import SuperblockImpactInput

DEFAULT_WALK_THRESHOLD_M = 400.0
POORLY_SERVED_SHARE = 0.5
# A stop farther than this from any street node is outside the network.
MAX_SNAP_DISTANCE_M = 150.0
METERS_PER_DEGREE_LAT = 110_540.0
METERS_PER_DEGREE_LON_EQUATOR = 111_320.0
_SOURCE = "__transit_stops__"


class TransitAccessChecker:
    def __init__(
        self,
        graph: nx.MultiDiGraph,
        stops: Iterable[TransitStop],
        source: str | None = None,
        walk_threshold_m: float = DEFAULT_WALK_THRESHOLD_M,
    ) -> None:
        self.graph = graph
        self.stops = list(stops)
        self.source = source
        self.walk_threshold_m = walk_threshold_m
        # Distances beyond the threshold are only reported up to this cap.
        self.search_cutoff_m = walk_threshold_m * 3

    def _snap(self) -> dict[int, tuple[float, str]]:
        """Nearest street node of each stop, with the snap distance and stop name."""
        nodes = [
            (node, data["x"], data["y"])
            for node, data in self.graph.nodes(data=True)
            if "x" in data and "y" in data
        ]
        if not nodes or not self.stops:
            return {}
        mean_lat = sum(item[2] for item in nodes) / len(nodes)
        x_scale = METERS_PER_DEGREE_LON_EQUATOR * math.cos(math.radians(mean_lat))
        points = np.array([(x * x_scale, y * METERS_PER_DEGREE_LAT) for _n, x, y in nodes])
        tree = cKDTree(points)
        queries = np.array(
            [
                (
                    stop.coordinates.lon * x_scale,
                    stop.coordinates.lat * METERS_PER_DEGREE_LAT,
                )
                for stop in self.stops
            ]
        )
        distances, indexes = tree.query(queries)
        snapped: dict[int, tuple[float, str]] = {}
        for stop, distance, index in zip(self.stops, distances, indexes, strict=True):
            if distance > MAX_SNAP_DISTANCE_M:
                continue
            node = nodes[int(index)][0]
            label = stop.name or f"{stop.mode} stop {stop.stop_id}"
            if node not in snapped or distance < snapped[node][0]:
                snapped[node] = (float(distance), label)
        return snapped

    def _walk_distances(
        self, snapped: dict[int, tuple[float, str]]
    ) -> tuple[dict[int, float], dict[int, str]]:
        walk = nx.Graph()
        for u, v, data in self.graph.edges(data=True):
            length = float(data.get("length", 0) or 0)
            if not walk.has_edge(u, v) or walk[u][v]["length"] > length:
                walk.add_edge(u, v, length=length)
        for node, (distance, _label) in snapped.items():
            if node in walk:
                walk.add_edge(_SOURCE, node, length=distance)
        if _SOURCE not in walk:
            return {}, {}
        distances, paths = nx.single_source_dijkstra(
            walk, _SOURCE, cutoff=self.search_cutoff_m, weight="length"
        )
        distances.pop(_SOURCE, None)
        nearest = {node: snapped[path[1]][1] for node, path in paths.items() if node != _SOURCE}
        return distances, nearest

    def check(self, superblocks: Iterable[SuperblockImpactInput]) -> TransitAccessSummary:
        items = list(superblocks)
        limitations = [
            "Walking distances use the road network without footpaths or passages, "
            "so real walks can be shorter."
        ]
        snapped = self._snap()
        if not snapped:
            return TransitAccessSummary(
                source=self.source,
                walk_threshold_m=self.walk_threshold_m,
                limitations=[
                    "No public transport stops were found near the street network, so "
                    "walking access to transit was not checked."
                ],
            )
        distances, nearest = self._walk_distances(snapped)
        snapped_nodes = set(snapped)

        results: list[SuperblockTransitAccess] = []
        for item in items:
            total = covered = 0.0
            worst = 0.0
            closest: tuple[float, str] | None = None
            nodes: set[int] = set()
            for u, v, key in item.interior_edges:
                if not self.graph.has_edge(u, v, key):
                    continue
                nodes.update((u, v))
                length = float(self.graph[u][v][key].get("length", 0) or 0)
                # Walk to the middle of the street, from whichever end is closer.
                reach = min(distances.get(u, math.inf), distances.get(v, math.inf))
                midpoint = reach + length / 2
                total += length
                if midpoint <= self.walk_threshold_m:
                    covered += length
                worst = max(worst, midpoint)
                for node in (u, v):
                    if node in distances and (closest is None or distances[node] < closest[0]):
                        closest = (distances[node], nearest[node])
            if total <= 0:
                continue
            boundary_nodes = {node for u, v, _key in item.boundary_edges for node in (u, v)}
            results.append(
                SuperblockTransitAccess(
                    superblock_id=item.superblock_id,
                    covered_share=round(covered / total, 3),
                    worst_walk_m=round(worst, 1) if math.isfinite(worst) else None,
                    stops_nearby=len(snapped_nodes & (nodes | boundary_nodes)),
                    nearest_stop=closest[1] if closest else None,
                )
            )

        mean_share = sum(item.covered_share for item in results) / len(results) if results else 0.0
        return TransitAccessSummary(
            checked=True,
            source=self.source,
            stops_used=len(snapped),
            walk_threshold_m=self.walk_threshold_m,
            mean_covered_share=round(mean_share, 3),
            superblocks_poorly_served=sum(
                1 for item in results if item.covered_share < POORLY_SERVED_SHARE
            ),
            superblocks=results,
            limitations=limitations,
        )
