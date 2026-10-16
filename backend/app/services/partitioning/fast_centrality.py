"""Sampled edge betweenness on a compiled shortest-path tree.

NetworkX's pure-Python Brandes implementation dominated city-scale runs (about
a minute for 128 sampled sources on Budapest). This version runs each sampled
Dijkstra in SciPy's compiled code and counts, for every edge, how many targets
sit below it in the source's shortest-path tree. With real-valued street
lengths, equal-length alternatives are rare, so a single tree per source ranks
edges the same way the full Brandes sum does for the percentile threshold the
partitioner uses.
"""

from __future__ import annotations

import random
from collections.abc import Hashable

import networkx as nx

try:  # pragma: no cover - import guard
    import numpy as np
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra
except ImportError:  # pragma: no cover - SciPy is a runtime dependency
    np = None


def sampled_edge_betweenness(
    graph: nx.Graph,
    k: int | None,
    weight: str = "weight",
    seed: int = 42,
) -> dict[tuple[Hashable, Hashable], float]:
    """Return unnormalized sampled edge betweenness for an undirected graph.

    Keys are the graph's own ``(u, v)`` edge tuples. Falls back to NetworkX
    when SciPy is unavailable.
    """
    if np is None:  # pragma: no cover - exercised only without SciPy
        return nx.edge_betweenness_centrality(graph, k=k, weight=weight, seed=seed)

    nodes = list(graph.nodes())
    if not nodes:
        return {}
    index = {node: position for position, node in enumerate(nodes)}

    rows: list[int] = []
    cols: list[int] = []
    weights: list[float] = []
    for u, v, data in graph.edges(data=True):
        length = max(float(data.get(weight, 1.0) or 1.0), 1e-6)
        a, b = index[u], index[v]
        rows.extend((a, b))
        cols.extend((b, a))
        weights.extend((length, length))
    size = len(nodes)
    matrix = csr_matrix((weights, (rows, cols)), shape=(size, size))

    if k is None or k >= size:
        sources = list(range(size))
    else:
        sources = [index[node] for node in random.Random(seed).sample(nodes, k)]

    # Edge totals keyed by (min_index, max_index) of the tree edge.
    totals: dict[tuple[int, int], float] = {}
    for source in sources:
        distances, predecessors = dijkstra(
            matrix, directed=False, indices=source, return_predecessors=True
        )
        reachable = np.flatnonzero(np.isfinite(distances))
        order = reachable[np.argsort(-distances[reachable], kind="stable")]
        below_list = [1.0] * size
        predecessor_list = predecessors.tolist()
        for node in order.tolist():
            parent = predecessor_list[node]
            if parent < 0:
                continue
            count = below_list[node]
            below_list[parent] += count
            key = (parent, node) if parent < node else (node, parent)
            # Each target below this node is one source-target path; the
            # source itself is not counted as a target.
            totals[key] = totals.get(key, 0.0) + count

    result: dict[tuple[Hashable, Hashable], float] = {}
    for u, v in graph.edges():
        a, b = index[u], index[v]
        key = (a, b) if a < b else (b, a)
        result[(u, v)] = totals.get(key, 0.0)
    return result
