"""Stage 2 — generate the video via the Pika MCP, and store it IN Redis.

The web app can't mint its own Pika token, so generation works two ways:

1. **Generation store (Redis):** every produced clip is cached by prompt hash,
   so a prompt that's already been rendered replays instantly — no re-render.
2. **Live Pika MCP:** with a connected Pika login (see pika_auth), we call
   `generate_video` with the Seedance 2.0 provider on
   `https://mcp.pika.me/api/mcp` and cache the result.

The rendered MP4 bytes are also pulled into Redis (`cerebra:vid:<hash>`), so the
video is **served straight out of Redis** via `/video/<hash>` — Redis holds the
media itself, not just a URL.
"""

from __future__ import annotations

import asyncio
import hashlib

import httpx

from . import pika_auth
from .config import settings
from .redis_store import get_redis


def _hash(prompt: str) -> str:
    # Include the complete generation profile so a prior Kling render—or a
    # Seedance render at another resolution/tier—can never satisfy this cache.
    source = f"{settings.generation_profile}\n{prompt.strip()}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]


def _key(prompt: str) -> str:
    return f"{settings.pika_gen_prefix}{_hash(prompt)}"


def _vid_key(h: str) -> str:
    return f"cerebra:vid:{h}"


def _src_key(h: str) -> str:
    return f"cerebra:vidsrc:{h}"


def get_cached_video(prompt: str) -> str | None:
    raw = get_redis().get(_key(prompt))
    return raw.decode("utf-8") if raw else None


def cache_video(prompt: str, served_url: str) -> None:
    get_redis().set(_key(prompt), served_url)


# --- video bytes in Redis ---------------------------------------------------


def ingest_to_redis(h: str, cdn_url: str) -> str:
    """Download the rendered MP4 into Redis. Returns the in-app served path
    (or the CDN URL as a fallback if the download fails)."""
    get_redis().set(_src_key(h), cdn_url)
    try:
        data = httpx.get(cdn_url, timeout=180, follow_redirects=True).content
        get_redis().set(_vid_key(h), data)
        return f"/api/video/{h}"
    except Exception:
        return cdn_url


def get_video_bytes(h: str) -> bytes | None:
    """Bytes for `/video/<h>`. Self-heals by re-fetching from the CDN if needed."""
    r = get_redis()
    data = r.get(_vid_key(h))
    if data:
        return data
    src = r.get(_src_key(h))
    if src:
        try:
            data = httpx.get(src.decode("utf-8"), timeout=180, follow_redirects=True).content
            r.set(_vid_key(h), data)
            return data
        except Exception:
            return None
    return None


def backfill_blobs() -> int:
    """Pull any already-rendered videos (CDN URLs in the gen store) into Redis."""
    r = get_redis()
    n = 0
    for key in r.scan_iter(match=f"{settings.pika_gen_prefix}*"):
        val = r.get(key)
        if not val:
            continue
        url = val.decode("utf-8")
        if not url.startswith("http"):
            continue  # already a served path
        h = key.decode("utf-8").split(":")[-1]
        served = ingest_to_redis(h, url)
        r.set(key, served)
        n += 1
    return n


# --- generation -------------------------------------------------------------


def _seedance_args(prompt: str, aspect_ratio: str, duration: int) -> dict:
    """Build only arguments accepted by Pika's Seedance provider.

    Seedance 2.0 rejects Kling-only ``negative_prompt``, ``shots``,
    ``quality_mode``, and ``prompt_adherence`` fields.
    """
    valid_aspects = {"9:16", "16:9", "1:1", "21:9", "4:3", "3:4"}
    valid_resolutions = {"480p", "720p", "1080p"}
    dur = max(4, min(15, duration or settings.default_duration))
    return {
        "provider": "seedance",
        "mode": "text_to_video",
        "prompt": prompt,
        "aspect_ratio": aspect_ratio if aspect_ratio in valid_aspects else "9:16",
        "duration": dur,
        "sound": settings.seedance_sound,
        "fast": settings.seedance_fast,
        "seedance_backend": (
            settings.seedance_backend
            if settings.seedance_backend in {"ark", "fal"}
            else "ark"
        ),
        "resolution": (
            settings.seedance_resolution
            if settings.seedance_resolution in valid_resolutions
            else "1080p"
        ),
        # Return immediately with a task id, then poll through task_status. This
        # avoids holding one MCP request open for the entire render.
        "background": True,
    }


def _extract_url(payload: dict) -> str | None:
    if not isinstance(payload, dict):
        return None
    for field in ("url", "video_url"):
        if payload.get(field):
            return payload[field]
    result = payload.get("result")
    if isinstance(result, dict):
        sc = result.get("structuredContent")
        if isinstance(sc, dict):
            for field in ("url", "video_url"):
                if sc.get(field):
                    return sc[field]
        for block in result.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text", "").startswith("http"):
                return block["text"].strip()
    return None


async def _mcp_generate_async(args: dict, token: str) -> str:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(settings.pika_mcp_url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("generate_video", arguments=args)
            data = res.structuredContent or {}
            url = _extract_url(data)
            if url:
                return url
            task_id = data.get("task_id")
            if not task_id:
                raise RuntimeError(f"Pika returned no url or task_id: {data}")
            for _ in range(90):
                st = await session.call_tool("task_status", arguments={"task_id": task_id})
                s = st.structuredContent or {}
                status = s.get("status")
                if status == "completed":
                    url = _extract_url(s) or _extract_url(s.get("result", {}))
                    if url:
                        return url
                    raise RuntimeError(f"Completed but no url: {s}")
                if status in {"failed", "cancelled"}:
                    raise RuntimeError(f"Pika task {status}")
            raise RuntimeError("Pika task timed out")


def generate(
    prompt: str, aspect_ratio: str = "9:16", duration: int = 10
) -> dict:
    """Return {status, video_url} for the phone to auto-play (served from Redis)."""
    cached = get_cached_video(prompt)
    if cached:
        return {
            "status": "completed",
            "video_url": cached,
            "cached": True,
            "provider": settings.video_provider,
            "model": settings.video_model,
            "resolution": settings.seedance_resolution,
        }

    token = pika_auth.get_access_token()
    if not token:
        return {
            "status": "unavailable",
            "message": (
                "Pika isn't connected yet. Run the one-time login: "
                "`pipeline/.venv/bin/python -m pipeline.pika_login`. After that the "
                "app generates videos forever — no token needed again."
            ),
        }

    try:
        cdn_url = asyncio.run(
            _mcp_generate_async(_seedance_args(prompt, aspect_ratio, duration), token)
        )
    except Exception as exc:
        return {"status": "error", "message": f"Pika generation failed: {exc}"}

    h = _hash(prompt)
    served = ingest_to_redis(h, cdn_url)  # store the MP4 bytes in Redis
    cache_video(prompt, served)
    return {
        "status": "completed",
        "video_url": served,
        "cdn_url": cdn_url,
        "provider": settings.video_provider,
        "model": settings.video_model,
        "resolution": settings.seedance_resolution,
    }
