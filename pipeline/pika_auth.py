"""Permanent Pika MCP auth — log in once, auto-refresh forever.

Pika's MCP is OAuth-only (no static API key). So "permanent" means: a one-time
browser login (`python -m pipeline.pika_login`) stores a long-lived **refresh
token**; this module exchanges it for short-lived access tokens automatically
before every generation, persisting the rotated refresh token. End users never
authenticate — only the developer, once.

Credentials persist in Redis (primary) + a gitignored file (fallback), so they
survive restarts. `PIKA_MCP_TOKEN` (a raw access token) still works as an
override for quick tests.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from .config import settings

AUTH_SERVER = "https://ecyvlzfbufloietjsmtj.supabase.co/auth/v1"
TOKEN_ENDPOINT = f"{AUTH_SERVER}/oauth/token"
AUTHORIZE_ENDPOINT = f"{AUTH_SERVER}/oauth/authorize"
REGISTRATION_ENDPOINT = f"{AUTH_SERVER}/oauth/clients/register"
RESOURCE = settings.pika_mcp_url  # https://mcp.pika.me/api/mcp

REDIS_KEY = "cerebra:pika_oauth"
CREDS_FILE = Path(__file__).resolve().parent / ".pika_creds.json"


def load_creds() -> dict | None:
    try:
        from .redis_store import get_redis

        raw = get_redis().get(REDIS_KEY)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    if CREDS_FILE.exists():
        try:
            return json.loads(CREDS_FILE.read_text())
        except Exception:
            return None
    return None


def save_creds(creds: dict) -> None:
    try:
        from .redis_store import get_redis

        get_redis().set(REDIS_KEY, json.dumps(creds))
    except Exception:
        pass
    try:
        CREDS_FILE.write_text(json.dumps(creds))
        CREDS_FILE.chmod(0o600)
    except Exception:
        pass


def is_connected() -> bool:
    return bool(settings.pika_mcp_token) or load_creds() is not None


def _refresh(creds: dict) -> dict:
    resp = httpx.post(
        TOKEN_ENDPOINT,
        data={
            "grant_type": "refresh_token",
            "refresh_token": creds["refresh_token"],
            "client_id": creds["client_id"],
        },
        headers={"accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    tok = resp.json()
    creds["access_token"] = tok["access_token"]
    creds["refresh_token"] = tok.get("refresh_token", creds["refresh_token"])
    creds["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    save_creds(creds)
    return creds


def get_access_token() -> str | None:
    """A valid access token, refreshing automatically. None if not logged in."""
    if settings.pika_mcp_token:
        return settings.pika_mcp_token
    creds = load_creds()
    if not creds:
        return None
    if creds.get("access_token") and creds.get("expires_at", 0) > time.time():
        return creds["access_token"]
    if creds.get("refresh_token"):
        try:
            return _refresh(creds)["access_token"]
        except Exception:
            return None
    return None
