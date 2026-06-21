"""Cerebra MCP server — TRIBE v2 cortical-engagement prediction as MCP tools.

This is the backend the `cerebra` plugin bundles via `.mcp.json`. It exposes
Meta's `facebook/tribev2` model over stdio so an agent can score a video's
population-average cortical-engagement curve and get trim-ready peak ranges,
then hand those to the Pika edit tools to auto-cut the highlight.

Run standalone:
    python worker/mcp_server.py            # stdio transport (for the plugin)

The TRIBE model is large and loads lazily on the first prediction so the MCP
handshake completes immediately on startup.

Scientific scope: TRIBE v2 predicts population-average cortical responses. The
four engagement "dimensions" are manually defined cortical surface proxies, not
measurements of emotion, reward, intent, or any individual viewer's mind.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from engagement import build_engagement

MODEL_ID = os.getenv("TRIBEV2_MODEL_ID", "facebook/tribev2")
CACHE_DIR = os.getenv("TRIBEV2_CACHE_DIR", str(Path(__file__).resolve().parent / ".cache"))
MAX_DOWNLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(1_000_000_000)))
_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".avi", ".mkv", ".m4v"}

mcp = FastMCP("cerebra")
_model = None  # lazily loaded TribeModel singleton


def _log(*parts: object) -> None:
    """Diagnostics go to stderr; stdout is reserved for the MCP JSON-RPC stream."""
    print("[cerebra-mcp]", *parts, file=sys.stderr, flush=True)


def _get_model():
    """Load the TRIBE v2 model once. Weights + feature extractors are large, so
    the first call is slow; subsequent predictions reuse the loaded model."""
    global _model
    if _model is None:
        _log(f"loading {MODEL_ID} (first call; this can take a while)…")
        from tribev2 import TribeModel

        _model = TribeModel.from_pretrained(MODEL_ID, cache_folder=CACHE_DIR)
        _log("model ready")
    return _model


def _resolve_video(video: str) -> tuple[str, tempfile.TemporaryDirectory]:
    """Stage `video` into a fresh temp dir and return (local_path, tmp_handle).

    Local files are copied and https:// URLs are downloaded into the temp dir, so
    every sidecar artifact TRIBE writes (e.g. the extracted .wav) lands in the
    temp dir and is cleaned up with it — never beside the user's original file.
    The caller must keep `tmp_handle` alive until the model has finished reading
    the file, then call `.cleanup()`.
    """
    candidate = Path(video).expanduser()
    if candidate.is_file():
        if candidate.suffix.lower() not in _VIDEO_SUFFIXES:
            raise ValueError(f"Unsupported video type {candidate.suffix!r}. Use one of {sorted(_VIDEO_SUFFIXES)}.")
        tmp = tempfile.TemporaryDirectory(prefix="cerebra-mcp-")
        dest = Path(tmp.name) / f"input{candidate.suffix.lower()}"
        shutil.copyfile(candidate, dest)
        return str(dest), tmp

    if video.startswith("http://") or video.startswith("https://"):
        suffix = Path(video.split("?", 1)[0]).suffix.lower()
        if suffix not in _VIDEO_SUFFIXES:
            suffix = ".mp4"
        tmp = tempfile.TemporaryDirectory(prefix="cerebra-mcp-")
        dest = Path(tmp.name) / f"input{suffix}"
        _log(f"downloading {video} → {dest}")
        try:
            with urllib.request.urlopen(video) as resp, dest.open("wb") as out:  # noqa: S310 (trusted user input)
                written = 0
                while chunk := resp.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_DOWNLOAD_BYTES:
                        raise ValueError("Video exceeds the download size limit.")
                    out.write(chunk)
        except Exception:
            tmp.cleanup()
            raise
        return str(dest), tmp

    raise FileNotFoundError(
        f"No video found at {video!r}. Pass a local file path or an https:// URL to a video."
    )


@mcp.tool()
def predict_engagement(video: str, top_peaks: int = 5) -> dict:
    """Predict a video's population-average cortical-engagement curve with TRIBE v2.

    Runs Meta's `facebook/tribev2` model on the video and returns a compact,
    time-resolved engagement summary plus ranked, trim-ready peak ranges — the
    raw material for auto-cutting to the most engaging moments.

    Args:
        video: Local path or https:// URL to a video (mp4/mov/webm/avi/mkv/m4v).
        top_peaks: How many non-overlapping engagement peaks to return (1–20).

    Returns a dict with:
        duration, frames, tr: timeline metadata (seconds, frame count, repetition time).
        global: per-frame overall engagement (0–100).
        regions: the four engagement dimensions, each with score + per-frame values,
            sorted strongest-first. Dimensions are cortical surface proxies
            (vmPFC/reward, aTEMP/emotional, lPFC/personal-relevance, vTEMP/memory).
        peaks: ranked peak moments as {rank, center_s, start_s, end_s, score,
            dimension, label} — feed start_s/end_s straight into a trim/cut tool.
        peak: the single strongest moment {time, label, value}.
    """
    top_peaks = max(1, min(int(top_peaks), 20))
    path, tmp = _resolve_video(video)
    try:
        model = _get_model()
        events = model.get_events_dataframe(video_path=path)
        predictions, _ = model.predict(events, verbose=False)
        result = build_engagement(predictions, float(model.data.TR), top_peaks=top_peaks)
        result["video"] = video
        return result
    finally:
        tmp.cleanup()


@mcp.tool()
def engagement_health() -> dict:
    """Report the TRIBE v2 model id and whether it is loaded yet.

    Does NOT trigger a load — use this to confirm the server is up before a
    (slow) first `predict_engagement` call."""
    return {"model": MODEL_ID, "loaded": _model is not None, "cache_dir": CACHE_DIR}


if __name__ == "__main__":
    mcp.run()  # stdio transport by default
