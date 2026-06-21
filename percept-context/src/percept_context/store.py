"""Redis connection + RedisVL vector index management.

Two clients are used:
  * `raw`  (decode_responses=False) — handed to RedisVL, which stores/reads
    binary float32 vectors in node hashes.
  * `kv`   (decode_responses=True)  — used for the graph layer (sorted-set
    adjacency, edge metadata, node-prop hashes) where we want plain strings.
"""

from __future__ import annotations

import numpy as np
import redis
from redis import Redis
from redis.backoff import ExponentialBackoff
from redis.retry import Retry
from redisvl.index import SearchIndex
from redisvl.schema import IndexSchema


def make_clients(settings) -> tuple[Redis, Redis]:
    """Two resilient clients (binary for RedisVL, decoded for the graph layer).

    Redis Cloud's free tier drops idle connections and occasionally stalls the
    first command on a cold DB, surfacing as read timeouts. We defend with a
    bounded socket timeout, keepalive, periodic health checks (ping-before-use
    reconnects stale sockets), and automatic retry with backoff.
    """
    common = dict(
        protocol=settings.redis_protocol,
        socket_timeout=30,
        socket_connect_timeout=15,
        socket_keepalive=True,
        health_check_interval=15,
        retry=Retry(ExponentialBackoff(cap=3.0, base=0.2), retries=5),
        retry_on_error=[redis.exceptions.TimeoutError, redis.exceptions.ConnectionError],
    )
    raw = Redis.from_url(settings.redis_url, decode_responses=False, **common)
    kv = Redis.from_url(settings.redis_url, decode_responses=True, **common)
    return raw, kv


def node_schema(settings, dims: int) -> IndexSchema:
    return IndexSchema.from_dict(
        {
            "index": {
                "name": settings.node_index,
                "prefix": settings.node_prefix,
                "storage_type": "hash",
            },
            "fields": [
                {"name": "id", "type": "tag"},
                {"name": "graph", "type": "tag"},
                {"name": "type", "type": "tag"},
                {"name": "label", "type": "text"},
                {"name": "content", "type": "text"},
                {"name": "score", "type": "numeric"},
                {
                    "name": "embedding",
                    "type": "vector",
                    "attrs": {
                        "dims": dims,
                        "distance_metric": "cosine",
                        "algorithm": "flat",
                        "datatype": "float32",
                    },
                },
            ],
        }
    )


def build_index(settings, dims: int, raw: Redis) -> SearchIndex:
    schema = node_schema(settings, dims)
    try:
        index = SearchIndex(schema, redis_client=raw)
    except TypeError:  # pragma: no cover - older RedisVL signatures
        index = SearchIndex(schema)
        index.set_client(raw)

    # Create the index once; tolerate "already exists".
    try:
        exists = index.exists()
    except Exception:
        exists = False
    if not exists:
        try:
            index.create(overwrite=False)
        except Exception as exc:  # pragma: no cover - race / already created
            if "Index already exists" not in str(exc):
                raise
    return index


def to_vector_bytes(vec) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()
