import networkx as nx
import pytest

from app.services.partitioning.fast_centrality import sampled_edge_betweenness


def _weighted_grid(size: int) -> nx.Graph:
    grid = nx.grid_2d_graph(size, size)
    graph = nx.Graph()
    for u, v in grid.edges():
        # Distinct lengths avoid ties so a single shortest-path tree is exact.
        graph.add_edge(u, v, weight=100 + (hash((u, v)) % 997) / 10)
    return graph


def test_matches_networkx_on_exact_run():
    graph = _weighted_grid(6)
    fast = sampled_edge_betweenness(graph, k=None)
    reference = nx.edge_betweenness_centrality(graph, weight="weight", normalized=False)
    for edge, value in reference.items():
        key = edge if edge in fast else (edge[1], edge[0])
        # NetworkX counts each unordered pair once on undirected graphs.
        assert fast[key] / 2 == pytest.approx(value)


def test_sampled_run_keys_every_edge_and_is_deterministic():
    graph = _weighted_grid(8)
    first = sampled_edge_betweenness(graph, k=10, seed=7)
    second = sampled_edge_betweenness(graph, k=10, seed=7)
    assert first == second
    assert set(first) == set(graph.edges())
    assert sum(first.values()) > 0
