"""Percept Context — a Redis-native context graph + GraphRAG engine, exposed over MCP.

Percept Context turns Redis into a property graph: nodes carry vector embeddings
(via RedisVL) and edges are reward-weighted relationships (Redis sorted sets).
Retrieval fuses semantic vector search (find entry nodes) with weighted graph
traversal (pull the connected, proven-to-perform subgraph) — i.e. GraphRAG.

Outcomes feed back: `record_outcome` reinforces the edges along a successful
path, so the graph *learns* which context performs and biases future retrieval.
"""

from .config import Settings, load_settings
from .graph import ContextGraph

__all__ = ["Settings", "load_settings", "ContextGraph", "__version__"]
__version__ = "0.1.0"
