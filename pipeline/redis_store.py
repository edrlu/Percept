"""Redis as a real-time context engine — beyond caching.

Three Redis-backed capabilities power Stage 1, all on one connection:

1. AdKnowledgeStore  — RedisVL vector index for RAG retrieval over a
   research-backed corpus of proven ad patterns (vector + hybrid search).
2. PromptCache       — a semantic cache (LangCache pattern): if a near-identical
   brief was optimized before, return that result instead of re-running the LLM.
3. AgentMemory       — a long-term log of every optimization run, so later
   stages and future sessions can learn from what was generated.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlparse

import redis
from redisvl.index import SearchIndex
from redisvl.query import VectorQuery
from redisvl.query.filter import Tag
from redisvl.schema import IndexSchema

from .config import settings
from .embeddings import embed_text, embedding_dims
from .schema import KnowledgeDoc, RetrievedDoc


_CLIENT: redis.Redis | None = None


class _DisposableConnectionPool(redis.ConnectionPool):
    """Reconnect after each operation for throttled Redis Cloud endpoints.

    The free Cloud proxy used by this project stops replying after a few
    commands on one TCP connection without returning a Redis error. Releasing
    and closing the socket keeps normal Redis/RedisVL APIs while ensuring the
    next operation receives a fresh authenticated connection.
    """

    def release(self, connection: redis.Connection) -> None:
        connection.disconnect()
        super().release(connection)


def get_redis() -> redis.Redis:
    """One shared, resilient connection pool for the index, cache, and memory.

    Free Redis endpoints cap concurrent connections and rate-limit new ones, so
    we reuse a single pooled client across all three stores instead of opening a
    fresh connection per object. Retries + keepalive ride out transient resets.
    """
    global _CLIENT
    if _CLIENT is None:
        from redis.backoff import ExponentialBackoff
        from redis.retry import Retry

        pool = _DisposableConnectionPool.from_url(
            settings.redis_url,
            protocol=settings.redis_protocol,
            # This Redis Cloud endpoint stalls on redis-py's optional
            # CLIENT SETINFO command. Authentication and commands work
            # normally when the library metadata handshake is omitted.
            lib_name=None,
            lib_version=None,
            decode_responses=False,
            socket_connect_timeout=10,
            socket_timeout=20,
            socket_keepalive=True,
            health_check_interval=0,
            max_connections=8,
            retry=Retry(ExponentialBackoff(cap=4.0, base=0.5), retries=5),
            retry_on_error=[redis.exceptions.ConnectionError, redis.exceptions.TimeoutError],
            retry_on_timeout=True,
        )
        _CLIENT = redis.Redis(connection_pool=pool)
    return _CLIENT


def redis_runtime_status() -> dict[str, Any]:
    """Verify the configured Redis connection and Vector Search index."""
    client = get_redis()
    pong = bool(client.ping())
    modules = client.execute_command("MODULE", "LIST")
    module_names: list[str] = []
    for module in modules:
        values = (
            module
            if isinstance(module, dict)
            else dict(zip(module[::2], module[1::2]))
        )
        raw_name = values.get(b"name", b"")
        module_names.append(
            raw_name.decode("utf-8") if isinstance(raw_name, bytes) else str(raw_name)
        )
    search_available = any(
        name.lower() in {"search", "ft", "redisearch"} for name in module_names
    )
    index_ready = False
    document_count = 0
    try:
        raw = client.execute_command("FT.INFO", settings.knowledge_index)
        info = raw if isinstance(raw, dict) else dict(zip(raw[::2], raw[1::2]))
        document_count = int(info.get(b"num_docs", 0))
        index_ready = document_count > 0
    except redis.exceptions.ResponseError:
        pass
    url = urlparse(settings.redis_url)
    return {
        "connected": pong,
        "endpoint": f"{url.hostname or 'unknown'}:{url.port or 6379}",
        "cloud": bool(url.hostname and url.hostname != "localhost"),
        "search_available": search_available,
        "knowledge_index": settings.knowledge_index,
        "index_ready": index_ready,
        "document_count": document_count,
    }


def _vector_field(dims: int) -> dict[str, Any]:
    return {
        "name": "embedding",
        "type": "vector",
        "attrs": {
            "dims": dims,
            "distance_metric": "cosine",
            "algorithm": "flat",  # exact search; the corpus is small
            "datatype": "float32",
        },
    }


# ---------------------------------------------------------------------------
# 1. Ad-knowledge vector index (RAG)
# ---------------------------------------------------------------------------


class AdKnowledgeStore:
    """RedisVL vector index over research-backed ad knowledge."""

    def __init__(self, client: redis.Redis | None = None) -> None:
        self.client = client or get_redis()
        dims = embedding_dims()
        schema = IndexSchema.from_dict(
            {
                "index": {
                    "name": settings.knowledge_index,
                    "prefix": settings.knowledge_prefix,
                    "storage_type": "hash",
                },
                "fields": [
                    {"name": "doc_id", "type": "tag"},
                    {"name": "title", "type": "text"},
                    {"name": "content", "type": "text"},
                    {"name": "category", "type": "tag"},
                    {"name": "industry", "type": "tag"},
                    {"name": "source", "type": "tag"},
                    {"name": "source_url", "type": "text"},
                    _vector_field(dims),
                ],
            }
        )
        self.index = SearchIndex(schema, redis_client=self.client)

    def ensure(self) -> None:
        """Create the index if it doesn't exist (idempotent)."""
        self.index.create(overwrite=False)

    def load(self, docs: list[KnowledgeDoc]) -> int:
        """Embed and upsert docs. Keyed on doc_id so re-loading is idempotent."""
        records = []
        for d in docs:
            records.append(
                {
                    "doc_id": d.id,
                    "title": d.title,
                    "content": d.content,
                    "category": d.category,
                    "industry": d.industry,
                    "source": d.source,
                    "source_url": d.source_url,
                    "embedding": embed_text(
                        f"{d.title}\n{d.content}", as_buffer=True
                    ),
                }
            )
        # A one-record transaction stays below this database endpoint's
        # observed per-socket command ceiling. The disposable pool reconnects
        # between transactions, while reads and vector queries remain normal.
        self.index.load(records, id_field="doc_id", batch_size=1)
        return len(records)

    def search(
        self,
        query: str,
        *,
        k: int = 6,
        industry: str | None = None,
    ) -> list[RetrievedDoc]:
        """Vector search, optionally hybrid-filtered by industry."""
        vq = VectorQuery(
            vector=embed_text(query),
            vector_field_name="embedding",
            return_fields=[
                "doc_id",
                "title",
                "content",
                "category",
                "industry",
                "source",
                "source_url",
            ],
            num_results=k,
            return_score=True,
        )
        if industry and industry != "general":
            # Hybrid: keep the requested industry OR cross-industry principles.
            vq.set_filter(Tag("industry") == [industry, "general"])

        out: list[RetrievedDoc] = []
        for r in self.index.query(vq):
            distance = float(r.get("vector_distance", 1.0))
            out.append(
                RetrievedDoc(
                    id=r.get("doc_id", ""),
                    title=r.get("title", ""),
                    content=r.get("content", ""),
                    category=r.get("category", ""),
                    industry=r.get("industry", ""),
                    source=r.get("source", ""),
                    source_url=r.get("source_url", ""),
                    score=round(1.0 - distance, 4),
                )
            )
        return out

    def audit(self) -> dict[str, Any]:
        """Return non-secret Redis index metadata for RAG provenance."""
        raw = self.client.execute_command("FT.INFO", settings.knowledge_index)
        info = raw if isinstance(raw, dict) else dict(zip(raw[::2], raw[1::2]))
        url = urlparse(settings.redis_url)
        endpoint = f"{url.hostname or 'unknown'}:{url.port or 6379}"
        return {
            "endpoint": endpoint,
            "index": settings.knowledge_index,
            "key_prefix": settings.knowledge_prefix,
            "storage_type": "hash",
            "vector_field": "embedding",
            "embedding_model": settings.embedding_model,
            "vector_dimensions": embedding_dims(),
            "distance_metric": "cosine",
            "index_document_count": int(info.get(b"num_docs", 0)),
        }


# ---------------------------------------------------------------------------
# 2. Semantic prompt cache (LangCache pattern)
# ---------------------------------------------------------------------------


class PromptCache:
    """Skip the LLM when a semantically-similar brief was optimized recently."""

    def __init__(self, client: redis.Redis | None = None) -> None:
        self.client = client or get_redis()
        dims = embedding_dims()
        schema = IndexSchema.from_dict(
            {
                "index": {
                    "name": settings.cache_index,
                    "prefix": settings.cache_prefix,
                    "storage_type": "hash",
                },
                "fields": [
                    {"name": "brief", "type": "text"},
                    {"name": "response", "type": "text"},  # JSON OptimizeResponse
                    _vector_field(dims),
                ],
            }
        )
        self.index = SearchIndex(schema, redis_client=self.client)

    def ensure(self) -> None:
        self.index.create(overwrite=False)

    def lookup(self, brief: str) -> dict | None:
        vq = VectorQuery(
            vector=embed_text(brief),
            vector_field_name="embedding",
            return_fields=["brief", "response"],
            num_results=1,
            return_score=True,
        )
        results = self.index.query(vq)
        if not results:
            return None
        hit = results[0]
        distance = float(hit.get("vector_distance", 1.0))
        if distance > settings.cache_distance:
            return None  # too dissimilar — treat as a miss
        try:
            return json.loads(hit["response"])
        except (KeyError, json.JSONDecodeError):
            return None

    def store(self, brief: str, response_json: str) -> None:
        self.index.load(
            [
                {
                    "brief": brief,
                    "response": response_json,
                    "embedding": embed_text(brief, as_buffer=True),
                }
            ]
        )


# ---------------------------------------------------------------------------
# 3. Long-term agent memory
# ---------------------------------------------------------------------------


class AgentMemory:
    """Append-only log of optimization runs, newest first, capped in length."""

    def __init__(self, client: redis.Redis | None = None) -> None:
        self.client = client or get_redis()

    def append(self, record: dict[str, Any]) -> None:
        record = {**record, "ts": time.time()}
        self.client.lpush(settings.memory_key, json.dumps(record).encode("utf-8"))
        self.client.ltrim(settings.memory_key, 0, settings.memory_max - 1)

    def recent(self, n: int = 20) -> list[dict[str, Any]]:
        raw = self.client.lrange(settings.memory_key, 0, n - 1)
        return [json.loads(item) for item in raw]
