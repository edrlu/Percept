"""Live web research on successful ads in the product's space.

A two-step Claude flow:
  1. `research_text` — web-search the space for high-performing ads and the
     tactics behind them (streamed, citation-aware).
  2. `structured` — distil that prose into a list of reusable ResearchFinding.

Findings are embedded and upserted into the SAME Redis vector index as the seed
corpus (source="research"), so the next retrieval — this request or a future
one — surfaces fresh, product-specific evidence alongside the timeless principles.
"""

from __future__ import annotations

from . import llm
from .redis_store import AdKnowledgeStore
from .schema import KnowledgeDoc, ResearchFinding

_FINDINGS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "technique": {"type": "string"},
                    "why_it_worked": {"type": "string"},
                    "industry": {"type": "string"},
                    "source_url": {"type": "string"},
                },
                "required": [
                    "title",
                    "technique",
                    "why_it_worked",
                    "industry",
                    "source_url",
                ],
            },
        }
    },
    "required": ["findings"],
}


def research_space(
    brief: str,
    *,
    product: str | None = None,
    industry: str | None = None,
    store: AdKnowledgeStore | None = None,
    persist: bool = True,
) -> list[ResearchFinding]:
    """Research successful ads for this brief and (optionally) cache to Redis."""
    if not llm.available():
        return []

    space = ", ".join(filter(None, [product, industry])) or "this product's category"
    prose = llm.research_text(
        system=(
            "You are an advertising-effectiveness researcher. Find recent, "
            "high-performing, REALISTIC short-form video ads relevant to the brief "
            "and identify the concrete, reusable tactics behind their success. "
            "Prefer specifics (named campaigns, formats, structures) over platitudes."
        ),
        user=(
            f"Brief: {brief}\n"
            f"Space: {space}\n\n"
            "Research what has worked for short-form video ads in this space. "
            "Surface 4–6 successful examples and the specific technique each used."
        ),
    )

    data = llm.structured(
        system=(
            "Convert the research notes into structured findings. Each finding is "
            "one reusable technique with a one-line reason it worked. Keep industry "
            "as a single lowercase word (e.g. beverage, tech, beauty, saas, food, "
            "fitness, general)."
        ),
        user=prose,
        schema=_FINDINGS_SCHEMA,
        max_tokens=2048,
    )

    findings = [ResearchFinding(**f) for f in data.get("findings", [])]

    if persist and findings:
        store = store or AdKnowledgeStore()
        store.ensure()
        docs = [
            KnowledgeDoc(
                id=f"research-{abs(hash((f.title, f.source_url))) % (10**12)}",
                title=f.title,
                content=f"Technique: {f.technique}\nWhy it worked: {f.why_it_worked}",
                category="research",
                industry=(f.industry or "general").lower(),
                source="research",
                source_url=f.source_url,
            )
            for f in findings
        ]
        store.load(docs)

    return findings
