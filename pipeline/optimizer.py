"""Stage 1 orchestration: brief → retrieval → research → optimized payload.

This is the first stage of the Cerebra loop. It takes the user's prompt
(voice→text or typed), grounds it in a research-backed RAG context pulled from
Redis, optionally refreshes that context with live web research, and emits a
fully-assembled, evidence-backed payload for the video model:

    Seedance 2.0 SYSTEM prompt + CONTEXT (research + vector retrieval) +
    Pika generation skill

It deliberately does NOT call Pika — Stage 2 owns generation.
"""

from __future__ import annotations

from . import llm, research
from .config import settings
from .duration import resolve_duration
from .knowledge.seedance_skill import assemble_payload
from .redis_store import AdKnowledgeStore, AgentMemory, PromptCache
from .schema import (
    OptimizedCreative,
    OptimizeRequest,
    OptimizeResponse,
    RAGTrace,
    ResearchFinding,
    RetrievedDoc,
)

_CREATIVE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "optimized_prompt": {"type": "string"},
        "generation_constraints": {
            "type": "array",
            "items": {"type": "string"},
        },
        "audio_direction": {"type": "string"},
        "aspect_ratio": {
            "type": "string",
            "enum": ["9:16", "1:1", "16:9", "3:4", "4:3", "21:9"],
        },
        "duration_seconds": {"type": "integer"},
        "hook": {"type": "string"},
        "style_tags": {"type": "array", "items": {"type": "string"}},
        "techniques_applied": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": [
        "optimized_prompt",
        "generation_constraints",
        "audio_direction",
        "aspect_ratio",
        "duration_seconds",
        "hook",
        "style_tags",
        "techniques_applied",
        "rationale",
    ],
}

_SEEDANCE_ASPECTS = {"9:16", "1:1", "16:9", "3:4", "4:3", "21:9"}


def _normalize_generation_settings(
    brief: str, aspect_ratio: str | None, duration: int | None
) -> tuple[str, int]:
    """Resolve user intent before cache lookup, retrieval, or LLM optimization."""
    aspect = aspect_ratio if aspect_ratio in _SEEDANCE_ASPECTS else "9:16"
    seconds = resolve_duration(brief, duration, settings.default_duration)
    return aspect, seconds


def _beat_guidance(duration: int) -> str:
    """Fit creative complexity to the actual clip length."""
    if duration <= 6:
        return (
            "The clip is extremely short: use exactly three compressed beats—"
            "HOOK, one REVEAL/ACTION beat, then BRANDED OUTRO. Keep dialogue to "
            "one very short line total and make every visual change immediately readable."
        )
    if duration <= 11:
        return (
            "Use four compact beats—HOOK, SETUP, REVEAL/PEAK, BRANDED OUTRO—with "
            "no dead frames and only concise dialogue that fits naturally."
        )
    return (
        "Use the full five-beat short-form arc—HOOK, three escalating beats, "
        "then BRANDED OUTRO."
    )


def _cache_brief(
    req: OptimizeRequest, aspect_ratio: str, duration: int
) -> str:
    """Model-aware cache input; prevents cross-setting and cross-model hits."""
    return "\n".join(
        [
            f"generation_profile={settings.generation_profile}",
            f"aspect_ratio={aspect_ratio}",
            f"duration_seconds={duration}",
            f"product={req.product or ''}",
            f"industry={req.industry or ''}",
            f"brief={req.brief.strip()}",
        ]
    )


def _retrieval_query(req: OptimizeRequest) -> str:
    """Embed all useful intake signals, not only the free-form sentence."""
    return "\n".join(
        [
            f"creative brief: {req.brief.strip()}",
            f"product or brand: {req.product or 'unspecified'}",
            f"industry: {req.industry or 'general'}",
            "retrieve advertising principles, successful reference patterns, "
            "short-form structure, visual craft, audio craft, and model-specific guidance",
        ]
    )


def _rag_trace(
    store: AdKnowledgeStore,
    query: str,
    retrieved: list[RetrievedDoc],
) -> RAGTrace:
    audit = store.audit()
    return RAGTrace(
        **audit,
        query=query,
        top_k=settings.top_k,
        retrieved_count=len(retrieved),
        retrieved_ids=[doc.id for doc in retrieved],
        retrieved_scores=[doc.score for doc in retrieved],
        verified=bool(retrieved) and audit["index_document_count"] > 0,
    )


def _context_block(retrieved: list[RetrievedDoc], findings: list[ResearchFinding]) -> str:
    lines: list[str] = [
        "DURATION PRECEDENCE: any 15-second timings in retrieved examples are "
        "structural references only; the resolved target duration in the optimized "
        "creative is authoritative and must not be expanded."
    ]
    if retrieved:
        lines.append("RETRIEVED PRINCIPLES & REFERENCES (Redis vector search):")
        for d in retrieved:
            lines.append(f"- [{d.category}/{d.industry}] {d.title} (sim {d.score}): {d.content}")
    if findings:
        lines.append("\nLIVE RESEARCH — SUCCESSFUL ADS IN THIS SPACE:")
        for f in findings:
            src = f" <{f.source_url}>" if f.source_url else ""
            lines.append(f"- {f.title}: {f.technique} — {f.why_it_worked}{src}")
    return "\n".join(lines) if lines else "(no context retrieved)"


def _optimize_with_llm(
    req: OptimizeRequest,
    retrieved: list[RetrievedDoc],
    findings: list[ResearchFinding],
    aspect_ratio: str,
    duration: int,
) -> OptimizedCreative:
    context = _context_block(retrieved, findings)
    pacing = _beat_guidance(duration)
    user = (
        f"USER BRIEF:\n{req.brief}\n\n"
        f"PRODUCT: {req.product or 'unspecified'}\n"
        f"INDUSTRY: {req.industry or 'unspecified'}\n"
        f"TARGET: {aspect_ratio}, {duration}s, 1080p Seedance 2.0 audio-video, "
        "generated FROM SCRATCH with native synchronized sound. This duration is "
        "authoritative; do not replace it with 10s or 15s.\n"
        f"PACING FOR THIS DURATION: {pacing}\n\n"
        f"EVIDENCE TO GROUND YOUR CHOICES:\n{context}\n\n"
        "Produce the optimized creative. optimized_prompt is the ready-to-run "
        "Seedance 2.0 prompt: a single chronological production brief with the "
        "duration-appropriate beat count specified above. Every timestamp must fit "
        f"inside 0–{duration}s, and the final timestamp must end at {duration}s. "
        "Establish a "
        "continuity anchor for the same subject, product, wardrobe, packaging, "
        "setting, and light. For each beat name one primary action, intentional "
        "camera behavior, performance, lighting, and synchronized audio. "
        "For any athletic or physical action, decompose the motion into preparation, "
        "planted support and weight transfer, joint rotation, contact, follow-through, "
        "and balanced recovery; direct gravity, friction, impact, object trajectory, "
        "and contact-synchronized foley. A kick must show support-foot plant, hip "
        "rotation, knee extension, instep contact, follow-through, and smooth ball "
        "acceleration without sliding, snapping, skipped contact, or teleporting. "
        "dialogue only when it improves the concept, and specify delivery plus "
        "lip-sync. Build to one clear audio-visual peak and branded final state. "
        "Seedance does not accept a negative_prompt: put positive quality and "
        "continuity requirements in optimized_prompt and summarize them in "
        "generation_constraints. audio_direction must cover ambience, foley, music, "
        "dialogue, and the sonic ending. Do not ask for tiny generated copy or dense "
        "UI, and do not invent claims or packaging text. "
        "Cite, in techniques_applied, which retrieved principles or research findings you used."
    )
    data = llm.structured(
        system=(
            "You are a world-class commercial director and prompt engineer for "
            "Seedance 2.0, a unified native audio-video model. Turn the brief into "
            "one coherent chronological prompt that maximizes attention and brand "
            "recall while remaining physically filmable. Give director-level control "
            "over action, performance, lighting, camera, continuity, dialogue, foley, "
            "ambience, and music. The video is generated from scratch. Seedance has "
            "The resolved TARGET duration is mandatory and overrides defaults or "
            "examples in any retrieved or installed skill text. For UGC, apply the "
            "installed pika:ugc-ads HOOK/cuts/OUTRO logic, compressed to the target "
            "duration rather than forcing its 15-second example. Complex physical "
            "actions must be biomechanically phased and physically "
            "causal: anticipation, weight transfer, contact, follow-through, recovery, "
            "with believable momentum, gravity, friction, and object response. "
            "no negative-prompt parameter, so express safeguards as positive stable "
            "requirements inside the prompt. Never output Kling shots or Kling-only "
            "settings. One idea, one feeling, win the first second, synchronize the "
            "audio-visual peak, and keep the brand attributable at the end."
        ),
        user=user,
        schema=_CREATIVE_SCHEMA,
    )
    # Runtime-validated settings always win over a model's attempted variation.
    data["aspect_ratio"] = aspect_ratio
    data["duration_seconds"] = duration
    data["model"] = settings.video_model
    data["resolution"] = settings.seedance_resolution
    data["sound"] = settings.seedance_sound
    return OptimizedCreative(**data)


def _optimize_template(
    req: OptimizeRequest,
    retrieved: list[RetrievedDoc],
    findings: list[ResearchFinding],
    aspect_ratio: str,
    duration: int,
) -> OptimizedCreative:
    """Deterministic fallback when no LLM key is configured.

    Assembles a usable multi-beat prompt from the brief and the top retrieved
    principles — less creative than the LLM path, but fully offline and runnable.
    """
    techniques = [d.title for d in retrieved[:5]] + [f.technique for f in findings[:3]]
    product = req.product or "the product"
    end = duration
    continuity = (
        f"Photorealistic {aspect_ratio} short-form product film for {product}, "
        f"exactly {duration} seconds, authentic premium social-ad texture. "
        "CONTINUITY: the same product, exact shape and packaging geometry, signature "
        "colors, real materials, and one consistent naturally lit setting throughout. "
    )
    physical_guardrails = (
        "Stable identity and anatomy, smooth natural motion, consistent exposure and "
        "product geometry, photoreal textures, clean frame without accidental text "
        "overlays or watermark. Any physical action follows visible preparation, "
        "planted support and weight transfer, anatomically natural joint rotation, "
        "exact contact, follow-through, and balanced recovery; impacted objects respond "
        "with coherent momentum, spin, gravity, and friction, synchronized to contact."
    )
    if duration <= 6:
        reveal_end = duration - 1
        prompt = (
            f"{continuity}"
            f"0–1s HOOK: open mid-action on the strongest product behavior from this "
            f"brief — {req.brief.strip()} — with one instantly readable camera move "
            f"and synchronized impact sound. 1–{reveal_end}s REVEAL/ACTION: show one "
            f"clear human use or physical demonstration, immediately reaching the "
            f"sensory peak with crisp foley and a restrained music lift. "
            f"{reveal_end}–{end}s OUTRO: land on a clean branded hero state, product "
            f"unchanged and clearly attributable, motion easing to a stable sonic finish. "
            f"{physical_guardrails}"
        )
    else:
        hook_end = 2
        peak_start = max(hook_end + 1, round(duration * 0.55))
        final_start = max(peak_start + 1, duration - 2)
        final_start = min(final_start, duration - 1)
        prompt = (
            f"{continuity}"
            f"0–{hook_end}s: open mid-action on the most visually arresting product "
            f"behavior from this brief — {req.brief.strip()} — with a fast controlled "
            f"macro push-in and synchronized impact sound. {hook_end}–{peak_start}s: "
            "reveal the human use or physical demonstration with one clear action, "
            "natural performance, stable hands, lifelike contact and weight, subtle "
            f"handheld energy, and coherent window light. {peak_start}–{final_start}s: "
            "reach the sensory and emotional peak; move closer to the key product "
            "transformation while foley becomes crisp and the restrained music lifts. "
            f"{final_start}–{end}s: settle into a clean branded hero state, product "
            "unchanged and clearly attributable, camera motion easing to a stable finish "
            f"as the sonic logo resolves. {physical_guardrails}"
        )
    return OptimizedCreative(
        optimized_prompt=prompt,
        generation_constraints=[
            "same subject, product, packaging geometry, wardrobe, and setting throughout",
            "stable anatomy, hands, identity, exposure, and realistic physical motion",
            "biomechanically phased action with visible contact, follow-through, and recovery",
            "one product only with a clean frame and no accidental overlays or watermark",
        ],
        audio_direction=(
            "Native synchronized ambience and close-mic product foley; restrained music "
            "builds into the peak and resolves with a short branded sonic ending."
        ),
        aspect_ratio=aspect_ratio,
        duration_seconds=duration,
        model=settings.video_model,
        resolution=settings.seedance_resolution,
        sound=settings.seedance_sound,
        hook="You have to see this.",
        style_tags=["photoreal", "short-form", "cinematic", "native audio"],
        techniques_applied=techniques,
        rationale=(
            "Template fallback: front-loads the payoff, one idea, realistic handheld "
            "look, branded across beats. Set ANTHROPIC_API_KEY for a fully optimized, "
            "research-driven creative."
        ),
    )


def optimize(req: OptimizeRequest) -> OptimizeResponse:
    """Run Stage 1 end to end and return the assembled video-model payload."""
    store = AdKnowledgeStore()
    store.ensure()
    cache = PromptCache()
    cache.ensure()
    memory = AgentMemory()

    aspect_ratio, duration = _normalize_generation_settings(
        req.brief, req.aspect_ratio, req.duration_seconds
    )
    cache_brief = _cache_brief(req, aspect_ratio, duration)
    use_cache = settings.use_semantic_cache if req.use_cache is None else req.use_cache
    if use_cache:
        cached = cache.lookup(cache_brief)
        if cached is not None:
            resp = OptimizeResponse(**cached)
            if (
                resp.creative.duration_seconds == duration
                and resp.creative.model == settings.video_model
            ):
                resp.cached = True
                return resp

    # RAG retrieval over the Redis-backed research corpus (+ prior research).
    retrieval_query = _retrieval_query(req)
    retrieved = store.search(
        retrieval_query, k=settings.top_k, industry=req.industry
    )
    if not retrieved:
        raise RuntimeError(
            f"Redis Vector Search returned no documents from "
            f"`{settings.knowledge_index}`; refusing to optimize without RAG context."
        )

    # Optional live research → also cached into the same Redis index.
    findings: list[ResearchFinding] = []
    if req.live_research:
        findings = research.research_space(
            req.brief, product=req.product, industry=req.industry, store=store
        )
        if findings:
            # Re-retrieve so fresh findings can rank into the context too.
            retrieved = store.search(
                retrieval_query, k=settings.top_k, industry=req.industry
            )
            if not retrieved:
                raise RuntimeError(
                    "Redis Vector Search returned no documents after research refresh."
                )

    rag = _rag_trace(store, retrieval_query, retrieved)
    if not rag.verified:
        raise RuntimeError("Redis RAG provenance verification failed.")

    llm_backed = llm.available()
    if llm_backed:
        creative = _optimize_with_llm(req, retrieved, findings, aspect_ratio, duration)
    else:
        creative = _optimize_template(req, retrieved, findings, aspect_ratio, duration)

    creative_block = (
        f"{creative.optimized_prompt}\n\n"
        f"MODEL: {creative.model} | RESOLUTION: {creative.resolution} | "
        f"SOUND: {str(creative.sound).lower()}\n"
        f"ASPECT RATIO: {creative.aspect_ratio} | DURATION: {creative.duration_seconds}s\n"
        f"AUDIO DIRECTION: {creative.audio_direction}\n"
        f"GENERATION CONSTRAINTS: {'; '.join(creative.generation_constraints)}\n"
        f"HOOK: {creative.hook}\n"
        f"STYLE: {', '.join(creative.style_tags)}\n"
        f"TECHNIQUES APPLIED: {', '.join(creative.techniques_applied)}\n"
        f"WHY IT WORKS: {creative.rationale}"
    )
    payload = assemble_payload(creative_block, _context_block(retrieved, findings))

    response = OptimizeResponse(
        creative=creative,
        video_model_payload=payload,
        brief=req.brief,
        cached=False,
        llm_backed=llm_backed,
        retrieved=retrieved,
        research=findings,
        rag=rag,
    )

    if use_cache:
        cache.store(cache_brief, response.model_dump_json())
    memory.append(
        {
            "brief": req.brief,
            "product": req.product,
            "industry": req.industry,
            "hook": creative.hook,
            "generation_profile": settings.generation_profile,
            "llm_backed": llm_backed,
        }
    )
    return response
