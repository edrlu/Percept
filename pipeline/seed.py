"""Build the Redis indices and load the research-backed seed corpus.

Run once after starting Redis:

    python -m pipeline.seed

Idempotent — safe to re-run. Creates the knowledge index + the semantic prompt
cache index, then embeds and upserts the seed corpus.
"""

from __future__ import annotations

from .knowledge.seed_corpus import SEED_DOCS
from .redis_store import AdKnowledgeStore, PromptCache


def main() -> None:
    store = AdKnowledgeStore()
    store.ensure()
    n = store.load(SEED_DOCS)
    print(f"Loaded {n} knowledge docs into '{store.index.name}'.")

    cache = PromptCache()
    cache.ensure()
    print(f"Ensured semantic prompt cache index '{cache.index.name}'.")
    print("Redis is ready for Stage 1 retrieval.")


if __name__ == "__main__":
    main()
