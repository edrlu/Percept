"""Percept Context MCP server (stdio).

Exposes the Redis context graph as MCP tools any agent can call. Built on the
official MCP Python SDK (FastMCP). The graph itself is lazily initialized on the
first tool call so the server starts fast.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import load_settings
from .graph import ContextGraph
from .seed import seed_graph

settings = load_settings()
cg = ContextGraph(settings)
mcp = FastMCP("percept-context")


def _graph(value: str) -> str | None:
    return value or None


@mcp.tool()
def graph_rag_query(
    query: str,
    graph: str = "",
    types: list[str] | None = None,
    k: int = 5,
    hops: int = 1,
) -> dict[str, Any]:
    """GraphRAG retrieval over the Redis context graph.

    Vector-search for the most semantically relevant entry nodes, then traverse
    reward-weighted edges outward `hops` steps to assemble the connected,
    proven-to-perform subgraph. Returns entry_nodes, the full node/edge subgraph,
    and a `context` string ready to ground an LLM prompt.

    Use this to answer "what proven creative principles/techniques apply to <brief>".
    Set `graph` to a user namespace (e.g. "user:dean") to query a personal graph.
    """
    return cg.graph_rag(query, graph=_graph(graph), types=types, k=k, hops=hops)


@mcp.tool()
def search_nodes(
    query: str, graph: str = "", types: list[str] | None = None, k: int = 5
) -> list[dict[str, Any]]:
    """Pure semantic vector search over graph nodes (no traversal).

    Returns the top-k nodes by cosine similarity, optionally filtered by node
    `types` (e.g. ["technique","principle"]) and `graph` namespace.
    """
    return cg.search(query, graph=_graph(graph), types=types, k=k)


@mcp.tool()
def add_node(
    type: str,
    label: str,
    content: str = "",
    props: dict[str, Any] | None = None,
    graph: str = "",
) -> dict[str, str]:
    """Add a node to the context graph (it is embedded and indexed for search).

    `type` is a free tag like "principle", "technique", "industry", "brief",
    "video", "score". Returns the new node_id.
    """
    node_id = cg.add_node(type, label, content, props=props, graph=_graph(graph))
    return {"node_id": node_id}


@mcp.tool()
def link_nodes(
    src_id: str,
    dst_id: str,
    type: str,
    weight: float = 1.0,
    props: dict[str, Any] | None = None,
    graph: str = "",
) -> dict[str, Any]:
    """Create a weighted, directed edge between two nodes.

    `type` is the relationship (e.g. "WORKS_IN", "ENABLES", "CO_OCCURS_WITH").
    `weight` is the edge strength / reward; higher edges are preferred during
    GraphRAG traversal.
    """
    return cg.link(src_id, dst_id, type, weight=weight, props=props, graph=_graph(graph))


@mcp.tool()
def neighbors(
    node_id: str,
    edge_type: str = "",
    direction: str = "out",
    graph: str = "",
    limit: int = 10,
) -> list[dict[str, Any]]:
    """List a node's neighbors, highest-weight first.

    `direction` is "out" or "in". Optionally restrict to a single `edge_type`.
    """
    return cg.neighbors(
        node_id, edge_type=edge_type or None, graph=_graph(graph),
        limit=limit, direction=direction,
    )


@mcp.tool()
def record_outcome(
    path: list[str], reward: float, graph: str = "", edge_type: str = "REWARDED"
) -> dict[str, Any]:
    """Close the loop: reinforce a path that performed well.

    Given an ordered list of node_ids (e.g. the principles/techniques used to
    make a winning ad, ending at the video node), increment the weight of the
    edges along that path by `reward` and bump each node's score. Future
    GraphRAG traversals then favor this proven combination. This is how a
    downstream signal (e.g. a TRIBE engagement score) teaches the graph.
    """
    return cg.record_outcome(path, reward, graph=_graph(graph), edge_type=edge_type)


@mcp.tool()
def top_performers(
    graph: str = "", types: list[str] | None = None, limit: int = 10
) -> list[dict[str, Any]]:
    """Return the highest-scoring nodes (most reinforced by outcomes)."""
    return cg.top_performers(graph=_graph(graph), types=types, limit=limit)


@mcp.tool()
def graph_stats(graph: str = "") -> dict[str, Any]:
    """Health + size of the graph: Redis ping, index name, node count, endpoint."""
    return cg.stats(graph=_graph(graph))


@mcp.tool()
def seed_demo_graph(graph: str = "") -> dict[str, Any]:
    """Load the bundled curated video-ad knowledge graph into `graph`.

    Call once on a fresh namespace to get a populated graph to query.
    """
    return seed_graph(cg, graph=_graph(graph))


@mcp.tool()
def compose_brief(brief: str, graph: str = "", k: int = 5, hops: int = 1) -> dict[str, Any]:
    """Full RAG pipeline: GraphRAG-retrieve context for a creative brief, then
    (if an Anthropic API key is configured) compose an optimized video-ad prompt
    grounded in that subgraph. Without a key, returns the assembled context so
    the calling agent can do the generation step itself.
    """
    rag = cg.graph_rag(brief, graph=_graph(graph), k=k, hops=hops)
    import os

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {
            "brief": brief,
            "grounded_context": rag["context"],
            "subgraph": {"nodes": rag["nodes"], "edges": rag["edges"]},
            "note": "Set ANTHROPIC_API_KEY (and pip install 'percept-context-plugin[llm]') to auto-compose the optimized prompt.",
        }
    try:
        import anthropic
    except ImportError:
        return {
            "brief": brief,
            "grounded_context": rag["context"],
            "note": "Install the LLM extra: pip install 'percept-context-plugin[llm]'.",
        }

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=settings.llm_model,
        max_tokens=900,
        messages=[
            {
                "role": "user",
                "content": (
                    "You are a short-form video-ad director. Using ONLY the proven "
                    "principles and relationships in the retrieved context graph below, "
                    "write a tight, director-level prompt for a 5–8s vertical ad for this "
                    "brief. Cite which principles/techniques you applied.\n\n"
                    f"BRIEF:\n{brief}\n\nCONTEXT GRAPH:\n{rag['context']}"
                ),
            }
        ],
    )
    text = "".join(getattr(b, "text", "") for b in msg.content)
    return {
        "brief": brief,
        "optimized_prompt": text,
        "grounded_context": rag["context"],
        "techniques_used": [n["label"] for n in rag["nodes"]],
    }


def main():
    """Console-script entrypoint: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
