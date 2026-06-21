"""Typed contracts for the Stage 1 pipeline.

These are the shapes that cross the boundary between intake → retrieval →
research → optimization → the assembled video-model payload (Stage 2 input).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class KnowledgeDoc(BaseModel):
    """One research-backed unit of ad knowledge stored in the Redis vector index."""

    id: str
    title: str
    content: str
    category: str = "principle"  # principle | reference_ad | structure | pika_craft | research
    industry: str = "general"  # beverage | tech | beauty | food | fitness | saas | general | ...
    source: str = "seed"  # seed | research
    source_url: str = ""


class RetrievedDoc(BaseModel):
    """A knowledge doc returned from vector search, with its similarity score."""

    id: str
    title: str
    content: str
    category: str
    industry: str
    source: str
    source_url: str = ""
    score: float = Field(0.0, description="Cosine similarity in [0,1]; higher is closer.")


class OptimizeRequest(BaseModel):
    brief: str = Field(..., description="User's raw creative brief (voice→text or typed).")
    product: str | None = Field(None, description="Product / brand name, if known.")
    industry: str | None = Field(None, description="Industry to bias retrieval (optional).")
    aspect_ratio: str | None = Field(None, description="9:16 | 1:1 | 16:9. Defaults to short-form 9:16.")
    duration_seconds: int | None = Field(
        None,
        description="Explicit target length. Otherwise read from the brief; defaults to 10s.",
    )
    live_research: bool = Field(False, description="Run live web research on successful ads in this space.")
    use_cache: bool | None = Field(None, description="Override the semantic cache for this request.")
    session_id: str | None = Field(
        None,
        description="Conversation/session id. When set, prior briefs in the same "
        "session are loaded from Redis as working memory and this turn is recorded.",
    )


class GenerateRequest(BaseModel):
    prompt: str = Field(..., description="The optimized generation prompt.")
    aspect_ratio: Literal["9:16", "1:1", "16:9", "3:4", "4:3", "21:9"] = "9:16"
    duration_seconds: int = Field(
        10, ge=4, le=15, description="Seedance 2.0 supports 4–15 seconds."
    )


class OptimizedCreative(BaseModel):
    """The LLM's research-backed creative decisions for a realistic short-form ad."""

    optimized_prompt: str = Field(
        ..., description="The ready-to-run Seedance 2.0 audio-video prompt."
    )
    generation_constraints: list[str] = Field(
        default_factory=list,
        description="Positive continuity and quality requirements integrated into the prompt.",
    )
    audio_direction: str = Field(
        "", description="Native dialogue, foley, ambience, and music direction."
    )
    aspect_ratio: str = "9:16"
    duration_seconds: int = 10
    model: str = "seedance-2.0"
    resolution: str = "1080p"
    sound: bool = True
    hook: str = Field("", description="The 0–3s scroll-stopping opening beat.")
    style_tags: list[str] = Field(default_factory=list)
    techniques_applied: list[str] = Field(
        default_factory=list, description="Which retrieved/research principles were used."
    )
    rationale: str = Field("", description="Why this will perform, tied to the evidence.")


class SessionTurn(BaseModel):
    """One message in a session's working memory (conversation history)."""

    role: Literal["user", "assistant"]
    content: str
    ts: float = 0.0


class ResearchFinding(BaseModel):
    title: str
    technique: str = Field(..., description="The reusable tactic the successful ad used.")
    why_it_worked: str
    industry: str = "general"
    source_url: str = ""


class RAGTrace(BaseModel):
    """Auditable proof that generation context came from Redis Vector Search."""

    backend: str = "redis"
    endpoint: str
    index: str
    key_prefix: str
    storage_type: str = "hash"
    vector_field: str = "embedding"
    embedding_model: str
    vector_dimensions: int
    distance_metric: str = "cosine"
    query: str
    top_k: int
    index_document_count: int
    retrieved_count: int
    retrieved_ids: list[str] = Field(default_factory=list)
    retrieved_scores: list[float] = Field(default_factory=list)
    verified: bool = True


class OptimizeResponse(BaseModel):
    """Stage 1 output — everything Stage 2 (Pika) needs, plus provenance."""

    creative: OptimizedCreative
    # The fully assembled message for the video model:
    #   Seedance SYSTEM prompt + context (research + retrieval) + generation skill.
    video_model_payload: str
    brief: str
    cached: bool = Field(False, description="True when the semantic cache served this result (no LLM call).")
    llm_backed: bool = Field(True, description="False if the deterministic template fallback ran.")
    retrieved: list[RetrievedDoc] = Field(default_factory=list)
    research: list[ResearchFinding] = Field(default_factory=list)
    rag: RAGTrace
    session_id: str | None = Field(None, description="Session this turn belongs to, if any.")
    session_turns: int = Field(
        0, description="Total messages in this session's Redis working memory after this turn."
    )
