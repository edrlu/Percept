"""Voice intake: audio file → text.

Primary path is the OpenAI Whisper API (set OPENAI_API_KEY). If no key is set,
we fall back to a local faster-whisper model when installed. The browser's Web
Speech API in the Studio UI is the lowest-latency path and needs neither — this
endpoint exists for server-side / uploaded-audio transcription.
"""

from __future__ import annotations

from functools import lru_cache

from .config import settings


class TranscriptionUnavailable(RuntimeError):
    pass


def _transcribe_openai(audio_path: str) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model=settings.whisper_model, file=f
        )
    return (result.text or "").strip()


@lru_cache(maxsize=1)
def _local_model():
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise TranscriptionUnavailable(
            "No OPENAI_API_KEY set and faster-whisper is not installed. Add an "
            "OpenAI key for Whisper, `pip install faster-whisper`, or use the "
            "browser Web Speech mic in the Studio."
        ) from exc
    return WhisperModel("base", device="cpu", compute_type="int8")


def _transcribe_local(audio_path: str) -> str:
    segments, _ = _local_model().transcribe(audio_path)
    return " ".join(seg.text.strip() for seg in segments).strip()


def transcribe(audio_path: str) -> str:
    """Transcribe an audio file to a single brief string."""
    if settings.whisper_available:
        return _transcribe_openai(audio_path)
    return _transcribe_local(audio_path)
