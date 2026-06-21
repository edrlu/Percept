"""Environment-driven configuration for the Stage 1 RAG optimizer.

Keys live in ONE place — the repo-root `.env.local` (gitignored). The frontend
already reads it; we load it here too so the Python service picks up the same
REDIS_URL / ANTHROPIC_API_KEY / OPENAI_API_KEY without a second copy. Real
environment variables always win over the file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_env_files() -> None:
    """Minimal .env loader (no dependency). Repo-root .env.local + pipeline/.env."""
    here = Path(__file__).resolve().parent
    candidates = [here.parent / ".env.local", here.parent / ".env", here / ".env"]
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:  # real env wins
                os.environ[key] = value


_load_env_files()


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _redis_url() -> str:
    """Accept either a full REDIS_URL or the separate Redis Cloud pieces.

    Redis Cloud gives you a public endpoint (host:port) and a default-user
    password. If REDIS_URL is already a proper URL (has '://') we use it as-is.
    Otherwise we assemble one from REDIS_HOST / REDIS_PORT / REDIS_PASSWORD —
    and if REDIS_URL was set to just the bare password, we use that as the
    password so a pasted token still works.
    """
    host = os.getenv("REDIS_HOST")
    port = os.getenv("REDIS_PORT")
    url = os.getenv("REDIS_URL")
    password = os.getenv("REDIS_PASSWORD") or (
        url if url and "://" not in url else None
    )
    if host and port:
        # The Redis workshop uses REDIS_USER; keep REDIS_USERNAME as an alias.
        user = os.getenv("REDIS_USER") or os.getenv("REDIS_USERNAME", "default")
        scheme = "rediss" if _bool("REDIS_TLS", False) else "redis"
        auth = f"{user}:{password}@" if password else ""
        return f"{scheme}://{auth}{host}:{port}"

    if url and "://" in url:
        return url

    return url or "redis://localhost:6379"


@dataclass(frozen=True)
class Settings:
    # --- Redis (qualified Redis prize tool: vector search + cache + memory) ---
    redis_url: str = _redis_url()
    redis_protocol: int = int(os.getenv("REDIS_PROTOCOL", "3"))

    # Vector index for the research-backed ad knowledge base (RAG retrieval).
    knowledge_index: str = os.getenv("CEREBRA_KNOWLEDGE_INDEX", "cerebra_ad_knowledge")
    knowledge_prefix: str = os.getenv("CEREBRA_KNOWLEDGE_PREFIX", "ad_knowledge")

    # Semantic prompt cache (LangCache pattern — skip recompute on similar briefs).
    # Versioned by generation model so a semantically similar brief can never
    # return a prompt optimized for the retired Kling pipeline.
    cache_index: str = os.getenv(
        "CEREBRA_CACHE_INDEX", "cerebra_prompt_cache_verified_rag_v4"
    )
    cache_prefix: str = os.getenv(
        "CEREBRA_CACHE_PREFIX", "prompt_cache_verified_rag_v4"
    )
    cache_distance: float = float(os.getenv("CEREBRA_CACHE_DISTANCE", "0.12"))

    # Long-term agent memory (every optimization run, newest-first).
    memory_key: str = os.getenv("CEREBRA_MEMORY_KEY", "cerebra:runs")
    memory_max: int = int(os.getenv("CEREBRA_MEMORY_MAX", "500"))

    # --- Embeddings (local, free, no key) ---
    embedding_model: str = os.getenv(
        "CEREBRA_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
    )

    # --- Retrieval ---
    top_k: int = int(os.getenv("CEREBRA_TOP_K", "6"))

    # --- LLM (prompt optimization + research synthesis) ---
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY")
    # Opus 4.8 is the current most-capable model; adaptive thinking is on by default.
    llm_model: str = os.getenv("CEREBRA_LLM_MODEL", "claude-opus-4-8")
    llm_effort: str = os.getenv("CEREBRA_LLM_EFFORT", "high")
    research_model: str = os.getenv("CEREBRA_RESEARCH_MODEL", "claude-opus-4-8")

    # --- Voice intake (OpenAI Whisper API; faster-whisper as offline fallback) ---
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    whisper_model: str = os.getenv("CEREBRA_WHISPER_MODEL", "whisper-1")

    # --- Stage 2: Pika generation (via the Pika MCP) ---
    pika_mcp_url: str = os.getenv("PIKA_MCP_URL", "https://mcp.pika.me/api/mcp")
    pika_mcp_token: str | None = os.getenv("PIKA_MCP_TOKEN")
    video_provider: str = os.getenv("CEREBRA_VIDEO_PROVIDER", "seedance")
    video_model: str = os.getenv("CEREBRA_VIDEO_MODEL", "seedance-2.0")
    seedance_backend: str = os.getenv("CEREBRA_SEEDANCE_BACKEND", "ark")
    seedance_resolution: str = os.getenv("CEREBRA_SEEDANCE_RESOLUTION", "1080p")
    seedance_fast: bool = _bool("CEREBRA_SEEDANCE_FAST", False)
    seedance_sound: bool = _bool("CEREBRA_SEEDANCE_SOUND", True)
    pika_gen_prefix: str = os.getenv(
        "CEREBRA_GEN_PREFIX", "cerebra:gen:seedance2:"
    )

    # --- Behaviour ---
    use_semantic_cache: bool = _bool("CEREBRA_USE_CACHE", True)
    default_aspect_ratio: str = os.getenv("CEREBRA_ASPECT_RATIO", "9:16")
    default_duration: int = int(os.getenv("CEREBRA_DURATION", "10"))

    # CORS for the Next.js frontend.
    cors_origins: list[str] = field(
        default_factory=lambda: os.getenv(
            "CORS_ORIGINS", "http://localhost:3000"
        ).split(",")
    )

    @property
    def llm_available(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def whisper_available(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def generation_profile(self) -> str:
        """Stable identity for semantic and rendered-video cache isolation."""
        tier = "fast" if self.seedance_fast else "quality"
        sound = "sound" if self.seedance_sound else "silent"
        return (
            f"{self.video_model}:{self.seedance_backend}:"
            f"{self.seedance_resolution}:{tier}:{sound}"
        )


settings = Settings()
