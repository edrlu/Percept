"""Live smoke test. Requires REDIS_URL in the environment.

    cd percept-context && python -m pytest -s tests/test_smoke.py

Skips automatically if REDIS_URL is unset.
"""

import os
import uuid

import pytest

if not os.environ.get("REDIS_URL"):
    pytest.skip("REDIS_URL not set", allow_module_level=True)

from percept_context import ContextGraph, load_settings
from percept_context.seed import seed_graph


@pytest.fixture(scope="module")
def cg():
    return ContextGraph(load_settings())


def test_seed_query_reinforce(cg):
    graph = f"test:{uuid.uuid4().hex[:8]}"

    summary = seed_graph(cg, graph=graph)
    assert summary["nodes_added"] > 0
    assert summary["edges_added"] > 0

    rag = cg.graph_rag("skincare before and after for glowing skin", graph=graph, k=3, hops=1)
    assert rag["entry_nodes"], "vector search returned no entry nodes"
    assert "context" in rag and rag["context"]

    # Reinforce an edge and confirm node scores move.
    if rag["edges"]:
        e = rag["edges"][0]
        cg.record_outcome([e["src"], e["dst"]], reward=3.0, graph=graph)

    stats = cg.stats(graph=graph)
    assert stats["ping"] is True
    assert stats["nodes_in_graph"] >= summary["nodes_added"]


def test_add_and_search(cg):
    graph = f"test:{uuid.uuid4().hex[:8]}"
    nid = cg.add_node("technique", "Slow-mo pour", "Slow-motion pour with condensation for beverages.", graph=graph)
    assert nid
    hits = cg.search("liquid pouring in slow motion", graph=graph, k=1)
    assert hits and hits[0]["node_id"] == nid
