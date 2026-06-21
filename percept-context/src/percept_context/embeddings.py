"""Pluggable embedding backends (RedisVL vectorizers).

Default is a local HuggingFace model (MiniLM, 384-dim) so the plugin works
offline with no API key. Set PERCEPT_VECTORIZER=openai to use OpenAI instead.
"""

from __future__ import annotations

from functools import lru_cache


def _import_hf():
    # Module path moved across RedisVL versions; support both.
    try:
        from redisvl.utils.vectorize import HFTextVectorizer  # type: ignore
    except ImportError:  # pragma: no cover - version shim
        from redisvl.utils.vectorizer import HFTextVectorizer  # type: ignore
    return HFTextVectorizer


def _import_openai():
    try:
        from redisvl.utils.vectorize import OpenAITextVectorizer  # type: ignore
    except ImportError:  # pragma: no cover - version shim
        from redisvl.utils.vectorizer import OpenAITextVectorizer  # type: ignore
    return OpenAITextVectorizer


@lru_cache(maxsize=2)
def get_vectorizer(kind: str, model: str):
    """Return a cached RedisVL vectorizer instance.

    The instance exposes `.dims` and `.embed(text) -> list[float]`.
    """
    kind = (kind or "hf").lower()
    if kind == "hf":
        return _import_hf()(model=model)
    if kind == "openai":
        # Fall back to a known embedding model if a HF id was left in env.
        oa_model = model if model.startswith("text-embedding") else "text-embedding-3-small"
        return _import_openai()(model=oa_model)
    raise ValueError(f"Unknown PERCEPT_VECTORIZER={kind!r} (expected 'hf' or 'openai')")
