"""Anthropic Claude wrapper for prompt optimization and research synthesis.

Thin, defensive layer over the official SDK:
- `available()` gates every LLM path so the pipeline degrades to a deterministic
  template when no ANTHROPIC_API_KEY is set.
- `structured()` forces schema-valid JSON via output_config.format.
- `research_text()` streams a web-search-backed answer.

Per project policy, all model IDs and API shapes follow the bundled claude-api
reference: Opus 4.8 with adaptive thinking, web_search_20260209, no budget_tokens.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from .config import settings


class LLMUnavailable(RuntimeError):
    pass


def available() -> bool:
    return settings.llm_available


@lru_cache(maxsize=1)
def _client():
    if not settings.llm_available:
        raise LLMUnavailable("ANTHROPIC_API_KEY is not set.")
    import anthropic

    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _first_text(content: Any) -> str:
    for block in content:
        if getattr(block, "type", None) == "text":
            return block.text
    return ""


def structured(
    *,
    system: str,
    user: str,
    schema: dict,
    max_tokens: int = 4096,
    model: str | None = None,
    effort: str | None = None,
) -> dict:
    """Return schema-valid JSON. Raises LLMUnavailable if no key is configured."""
    client = _client()
    msg = client.messages.create(
        model=model or settings.llm_model,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        output_config={
            "effort": effort or settings.llm_effort,
            "format": {"type": "json_schema", "schema": schema},
        },
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if msg.stop_reason == "refusal":
        raise LLMUnavailable("Model refused the request.")
    return json.loads(_first_text(msg.content))


def research_text(*, system: str, user: str, max_tokens: int = 8000) -> str:
    """Stream a web-search-backed research answer; returns the final text."""
    client = _client()
    with client.messages.stream(
        model=settings.research_model,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        output_config={"effort": settings.llm_effort},
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 6}],
        system=system,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        final = stream.get_final_message()
    # The model may interleave several text blocks (around tool calls); join them.
    return "\n".join(
        b.text for b in final.content if getattr(b, "type", None) == "text"
    ).strip()
