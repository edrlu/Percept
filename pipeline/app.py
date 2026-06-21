"""FastAPI service for Stage 1 — the research-backed RAG prompt optimizer.

Mirrors the worker/ pattern: a thin Python service the Next.js app calls. It
turns a brief (voice or text) into the assembled Seedance 2.0 payload, but never
calls Pika — that is Stage 2.

Endpoints:
  GET  /health             — readiness + whether the LLM path is live
  POST /optimize           — brief (JSON) → assembled payload
  POST /transcribe         — audio file → text (optional, faster-whisper)
  POST /research/refresh    — live-research a space and cache findings to Redis
  GET  /memory             — recent optimization runs (agent memory)

Run:  uvicorn pipeline.app:app --port 8100
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import llm, optimizer, pika, pika_auth, research
from .config import settings
from .duration import DurationRequestError
from .redis_store import AdKnowledgeStore, AgentMemory, SessionMemory, redis_runtime_status
from .schema import GenerateRequest, OptimizeRequest, OptimizeResponse

app = FastAPI(title="Cerebra Stage 1 — RAG prompt optimizer")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    try:
        redis_status = redis_runtime_status()
    except Exception as exc:
        redis_status = {
            "connected": False,
            "endpoint": "unavailable",
            "cloud": False,
            "search_available": False,
            "knowledge_index": settings.knowledge_index,
            "index_ready": False,
            "document_count": 0,
            "error": str(exc),
        }
    return {
        "ready": True,
        "llm_backed": llm.available(),
        "model": settings.llm_model if llm.available() else None,
        "whisper": settings.whisper_available,
        "pika_connected": pika_auth.is_connected(),
        "video_provider": settings.video_provider,
        "video_model": settings.video_model,
        "video_resolution": settings.seedance_resolution,
        "generation_profile": settings.generation_profile,
        "redis": redis_status,
    }


@app.post("/optimize", response_model=OptimizeResponse)
def optimize(req: OptimizeRequest) -> OptimizeResponse:
    if not req.brief.strip():
        raise HTTPException(status_code=422, detail="Brief is empty.")
    try:
        return optimizer.optimize(req)
    except DurationRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # surface a clean error to the frontend
        raise HTTPException(status_code=500, detail=f"Optimization failed: {exc}") from exc


@app.post("/generate")
def generate(req: GenerateRequest) -> dict:
    """Stage 2 — render through Pika's Seedance 2.0 provider and return the URL."""
    if not req.prompt.strip():
        raise HTTPException(status_code=422, detail="Prompt is empty.")
    return pika.generate(
        req.prompt,
        aspect_ratio=req.aspect_ratio,
        duration=req.duration_seconds,
    )


@app.get("/video/{vid}")
def video(vid: str, request: Request) -> Response:
    """Stream a rendered video straight out of Redis (with Range support)."""
    data = pika.get_video_bytes(vid)
    if data is None:
        raise HTTPException(status_code=404, detail="Video not in store.")
    total = len(data)
    rng = request.headers.get("range")
    if rng and rng.startswith("bytes="):
        first, _, last = rng[6:].partition("-")
        start = int(first) if first else 0
        end = int(last) if last else total - 1
        end = min(end, total - 1)
        chunk = data[start : end + 1]
        return Response(
            content=chunk,
            status_code=206,
            media_type="video/mp4",
            headers={
                "Content-Range": f"bytes {start}-{end}/{total}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(len(chunk)),
                "Cache-Control": "public, max-age=86400",
            },
        )
    return Response(
        content=data,
        media_type="video/mp4",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(total),
            "Cache-Control": "public, max-age=86400",
        },
    )


@app.post("/transcribe")
async def transcribe_audio(audio: UploadFile = File(...)) -> dict:
    from . import transcribe as _t

    suffix = Path(audio.filename or "clip.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(await audio.read())
        tmp.flush()
        try:
            text, engine = _t.transcribe_with_engine(tmp.name)
        except _t.TranscriptionUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Transcription failed: {exc}") from exc
    return {"text": text, "engine": engine}


@app.post("/research/refresh")
def research_refresh(req: OptimizeRequest) -> dict:
    if not llm.available():
        raise HTTPException(status_code=503, detail="No ANTHROPIC_API_KEY configured.")
    store = AdKnowledgeStore()
    store.ensure()
    findings = research.research_space(
        req.brief, product=req.product, industry=req.industry, store=store
    )
    return {"count": len(findings), "findings": [f.model_dump() for f in findings]}


@app.get("/memory")
def memory(n: int = 20) -> dict:
    return {"runs": AgentMemory().recent(n)}


@app.get("/session/{session_id}")
def session_history(session_id: str, n: int = 20) -> dict:
    """Per-session conversation history (working memory) from Redis."""
    mem = SessionMemory()
    return {
        "session_id": session_id,
        "turns": mem.history(session_id, n=n),
        "turn_count": mem.turn_count(session_id),
    }
