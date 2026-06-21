"""Local embedding model via RedisVL's HuggingFace vectorizer.

Free, offline, no API key — `sentence-transformers/all-MiniLM-L6-v2` (384-dim).
Loaded once and shared across the knowledge index, the semantic cache, and
query-time encoding so every vector lives in the same space.
"""

from __future__ import annotations

from functools import lru_cache

from redisvl.utils.vectorize import HFTextVectorizer

from .config import settings


@lru_cache(maxsize=1)
def get_vectorizer() -> HFTextVectorizer:
    """The model download (~80MB) happens on first call, then it's cached."""
    return HFTextVectorizer(model=settings.embedding_model)


def embedding_dims() -> int:
    return get_vectorizer().dims


def embed_text(text: str, *, as_buffer: bool = False):
    """Encode one string. `as_buffer=True` returns the float32 bytes Redis stores."""
    # RedisVL's HFTextVectorizer defaults to float32; don't forward a dtype kwarg
    # (newer sentence-transformers rejects unknown encode() kwargs).
    return get_vectorizer().embed(text, as_buffer=as_buffer)
