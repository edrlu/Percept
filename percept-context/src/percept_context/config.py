"""Environment-driven configuration for Percept Context."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load a local .env if present (no-op when running under an MCP client that
# already injects env vars).
load_dotenv()


@dataclass(frozen=True)
class Settings:
    redis_url: str
    redis_protocol: int
    node_index: str
    node_prefix: str
    default_graph: str
    vectorizer: str
    embedding_model: str
    llm_model: str


def load_settings() -> Settings:
    url = os.environ.get("REDIS_URL")
    if not url:
        raise RuntimeError(
            "REDIS_URL is not set. Copy .env.example to .env and set your "
            "Redis connection string (local or Redis Cloud)."
        )
    return Settings(
        redis_url=url,
        redis_protocol=int(os.environ.get("REDIS_PROTOCOL", "2")),
        node_index=os.environ.get("PERCEPT_INDEX", "percept_nodes"),
        node_prefix=os.environ.get("PERCEPT_PREFIX", "percept:node"),
        default_graph=os.environ.get("PERCEPT_DEFAULT_GRAPH", "shared"),
        vectorizer=os.environ.get("PERCEPT_VECTORIZER", "hf"),
        embedding_model=os.environ.get(
            "PERCEPT_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        ),
        llm_model=os.environ.get("PERCEPT_LLM_MODEL", "claude-opus-4-8"),
    )
