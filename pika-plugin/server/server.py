"""Cerebra neuro-engagement scoring server.

Wraps ``facebook/tribev2`` behind a small FastAPI API. The important reliability
property is that text is an optional enhancement, not a single point of failure:

* ``TRIBEV2_TEXT_MODE=auto`` (default) uses WhisperX + LLaMA when a token with
  LLaMA 3.2 access is available, then retries with the configured ungated
  modalities if either stage fails.
* ``TRIBEV2_TEXT_MODE=off`` needs no gated model. ``TRIBEV2_MODALITIES=video``
  is the universal T4 default; ``video,audio`` enables Wav2Vec-BERT too.
* ``TRIBEV2_TEXT_MODE=required`` fails clearly instead of silently degrading.

The public TRIBE checkpoint is still the same multimodal brain model in every
mode. Missing modality features are represented by zeros by TRIBE's own
inference pipeline.
"""

from __future__ import annotations

import gc
import os
import tempfile
import threading
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
import torch
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from ad_score import FEATURE_WEIGHTS, build_ad_score
from engagement import EngagementScorer
from video_features import (
    MAX_CLIP_SECONDS,
    extract_yolo_features,
    normalize_short_video,
)

torch.set_float32_matmul_precision("high")
torch.backends.cudnn.allow_tf32 = True

MODEL_ID = os.getenv("TRIBEV2_MODEL_ID", "facebook/tribev2")
LLAMA_MODEL_ID = os.getenv("TRIBEV2_TEXT_MODEL_ID", "meta-llama/Llama-3.2-3B")
CACHE_DIR = Path(
    os.getenv(
        "TRIBEV2_CACHE_DIR",
        str(Path(__file__).resolve().parent / ".cache"),
    )
)
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(200_000_000)))
API_KEY = os.getenv("NEURO_API_KEY", "").strip()
TEXT_MODE = os.getenv("TRIBEV2_TEXT_MODE", "auto").strip().lower()
INFERENCE_MODALITIES = tuple(
    item.strip().lower()
    for item in os.getenv("TRIBEV2_MODALITIES", "video").split(",")
    if item.strip()
)
UPSTREAM_COMMIT = os.getenv("TRIBEV2_UPSTREAM_COMMIT", "unknown")
SERVER_VERSION = "0.3.0"
MAX_LOOP_ITERATIONS = 5
VIDEO_MODEL_PATH = os.getenv("TRIBEV2_VIDEO_MODEL_PATH", "").strip()
AUDIO_MODEL_PATH = os.getenv("TRIBEV2_AUDIO_MODEL_PATH", "").strip()
_BUNDLED_YOLO = Path(__file__).resolve().parent / "assets" / "yolov8n.pt"
YOLO_MODEL = os.getenv(
    "YOLO_MODEL",
    str(_BUNDLED_YOLO) if _BUNDLED_YOLO.exists() else "yolov8n.pt",
)

if TEXT_MODE not in {"auto", "off", "required"}:
    raise RuntimeError(
        "TRIBEV2_TEXT_MODE must be one of: auto, off, required "
        f"(got {TEXT_MODE!r})"
    )
if not INFERENCE_MODALITIES or INFERENCE_MODALITIES[0] != "video":
    raise RuntimeError(
        "TRIBEV2_MODALITIES must be `video` or `video,audio`; "
        f"got {','.join(INFERENCE_MODALITIES)!r}"
    )
if any(item not in {"video", "audio"} for item in INFERENCE_MODALITIES):
    raise RuntimeError(
        "TRIBEV2_MODALITIES supports only `video` and optional `audio`; "
        f"got {','.join(INFERENCE_MODALITIES)!r}"
    )

_state: dict[str, Any] = {
    "model": None,
    "scorer": None,
    "startup_error": None,
    "last_error": None,
    "text_enabled": False,
    "text_reason": "not checked",
}
_inference_lock = threading.Lock()


class PipelineFailure(RuntimeError):
    """A model-pipeline failure with enough context to debug remotely."""

    def __init__(
        self,
        stage: str,
        exc: BaseException,
        *,
        fallback_exc: BaseException | None = None,
    ):
        super().__init__(str(exc))
        self.stage = stage
        self.original = exc
        self.fallback_exc = fallback_exc

    def payload(self) -> dict[str, Any]:
        payload = {
            "error": "inference_failed",
            "stage": self.stage,
            "type": type(self.original).__name__,
            "message": str(self.original),
            "hint": _failure_hint(self.original),
        }
        if self.fallback_exc is not None:
            payload["fallback"] = {
                "type": type(self.fallback_exc).__name__,
                "message": str(self.fallback_exc),
                "hint": _failure_hint(self.fallback_exc),
            }
        return payload


def _failure_hint(exc: BaseException) -> str:
    message = f"{type(exc).__name__}: {exc}".lower()
    if any(x in message for x in ("gated", "401", "403", "llama", "access to model")):
        return (
            "The Hugging Face token cannot read meta-llama/Llama-3.2-3B. "
            "Accept that model's license or use TRIBEV2_TEXT_MODE=off/auto."
        )
    if any(x in message for x in ("uvx", "whisperx", "transcrib")):
        return (
            "WhisperX transcription failed. Install `uv`, or use the default "
            "auto mode so scoring retries with the configured ungated modalities."
        )
    if any(x in message for x in ("out of memory", "cuda", "cublas", "cudnn")):
        return (
            "GPU inference failed, commonly from T4 memory pressure. Auto mode "
            "retries without the LLaMA text encoder; shorten the clip if needed."
        )
    if any(x in message for x in ("ffmpeg", "moviepy", "decode", "moov atom")):
        return "The upload could not be decoded. Re-encode it as SDR H.264/AAC MP4."
    if any(x in message for x in ("xet", "huggingface", "download", "cdn.hf.co")):
        return (
            "A Hugging Face encoder download failed. Run the notebook's encoder "
            "prefetch cell; it bypasses hf-xet, verifies file size/SHA-256, and "
            "loads V-JEPA2 from a local directory."
        )
    return "Inspect /diagnostics and the server log for the exact failing stage."


def _release_transient_memory() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _hf_token() -> str:
    return (
        os.getenv("HF_TOKEN")
        or os.getenv("HUGGING_FACE_HUB_TOKEN")
        or os.getenv("HUGGINGFACE_HUB_TOKEN")
        or ""
    ).strip()


def _resolve_text_capability() -> tuple[bool, str]:
    if TEXT_MODE == "off":
        return False, "disabled by TRIBEV2_TEXT_MODE=off"

    token = _hf_token()
    if not token:
        reason = (
            "HF_TOKEN is not set; using reliable "
            f"{'+'.join(INFERENCE_MODALITIES)} mode"
        )
        if TEXT_MODE == "required":
            reason += " (required mode will reject scoring)"
        return False, reason

    try:
        from huggingface_hub import hf_hub_download

        # A tiny gated file is a real authorization check. Merely querying model
        # metadata succeeds even when the account has not accepted the license.
        hf_hub_download(
            LLAMA_MODEL_ID,
            "config.json",
            token=token,
            cache_dir=str(CACHE_DIR / "huggingface"),
        )
    except Exception as exc:
        reason = (
            f"HF_TOKEN cannot access {LLAMA_MODEL_ID}: "
            f"{type(exc).__name__}: {exc}"
        )
        if TEXT_MODE == "required":
            reason += " (required mode will reject scoring)"
        return False, reason
    return True, f"authorized for {LLAMA_MODEL_ID}"


def _local_encoder_overrides() -> dict[str, str]:
    """Point TRIBE's extractors at verified local model snapshots when supplied."""
    overrides: dict[str, str] = {}
    local_paths = [path for path in (VIDEO_MODEL_PATH, AUDIO_MODEL_PATH) if path]
    if local_paths:
        from neuralset.extractors.base import HuggingFaceMixin

        for raw_path in local_paths:
            path = Path(raw_path)
            if not path.is_dir():
                raise FileNotFoundError(f"local encoder directory does not exist: {path}")
            if not (path / "config.json").is_file():
                raise FileNotFoundError(f"local encoder config is missing: {path / 'config.json'}")
            if not (path / "model.safetensors").is_file():
                raise FileNotFoundError(
                    f"local encoder weights are missing: {path / 'model.safetensors'}"
                )
            value = str(path)
            if value not in HuggingFaceMixin._REPOS:
                HuggingFaceMixin._REPOS.append(value)

    if VIDEO_MODEL_PATH:
        overrides["data.video_feature.image.model_name"] = VIDEO_MODEL_PATH
    if AUDIO_MODEL_PATH:
        overrides["data.audio_feature.model_name"] = AUDIO_MODEL_PATH
    return overrides


@asynccontextmanager
async def lifespan(_: FastAPI):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _state["text_enabled"], _state["text_reason"] = _resolve_text_capability()

    try:
        from tribev2 import TribeModel

        if not torch.cuda.is_available():
            print(
                "[neuro] WARNING: CUDA unavailable; inference will be very slow.",
                flush=True,
            )
        print(
            f"[neuro] loading {MODEL_ID}; text={_state['text_enabled']} "
            f"({_state['text_reason']})",
            flush=True,
        )
        encoder_overrides = _local_encoder_overrides()
        model = TribeModel.from_pretrained(
            MODEL_ID,
            cache_folder=str(CACHE_DIR),
            config_update=encoder_overrides or None,
        )
        scorer = EngagementScorer(
            cache_dir=CACHE_DIR,
            tr_seconds=float(model.data.TR),
        )
        try:
            scorer.warmup()
        except Exception as exc:
            print(f"[neuro] atlas warmup deferred: {exc}", flush=True)
        _state["model"], _state["scorer"] = model, scorer
        print("[neuro] ready.", flush=True)
    except Exception as exc:
        _state["startup_error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        print(f"[neuro] startup failed:\n{traceback.format_exc()}", flush=True)

    yield
    _state["model"] = _state["scorer"] = None
    _release_transient_memory()


app = FastAPI(title="Cerebra neuro-engagement scorer", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


def _check_key(provided: str | None) -> None:
    if API_KEY and (provided or "").strip() != API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing x-api-key")


def _runtime_info() -> dict[str, Any]:
    cuda = torch.cuda.is_available()
    return {
        "ready": _state["model"] is not None,
        "server_version": SERVER_VERSION,
        "model": MODEL_ID,
        "upstream_commit": UPSTREAM_COMMIT,
        "cuda": cuda,
        "device": torch.cuda.get_device_name(0) if cuda else "cpu",
        "torch": torch.__version__,
        "auth": bool(API_KEY),
        "text_mode": TEXT_MODE,
        "text_enabled": _state["text_enabled"],
        "text_reason": _state["text_reason"],
        "inference_modalities": list(INFERENCE_MODALITIES),
        "video_model_path": VIDEO_MODEL_PATH or "huggingface",
        "audio_model_path": AUDIO_MODEL_PATH or "huggingface",
        "max_clip_seconds": MAX_CLIP_SECONDS,
        "max_loop_iterations": MAX_LOOP_ITERATIONS,
        "ad_score_feature_weights": FEATURE_WEIGHTS,
        "yolo_model": YOLO_MODEL,
        "startup_error": _state["startup_error"],
    }


@app.get("/health")
def health():
    return _runtime_info()


@app.get("/diagnostics")
def diagnostics(x_api_key: str | None = Header(default=None)):
    _check_key(x_api_key)
    return {**_runtime_info(), "last_error": _state["last_error"]}


def _base_video_events(video_path: str):
    import pandas as pd
    from neuralset.events.utils import standardize_events

    return standardize_events(
        pd.DataFrame(
            [
                {
                    "type": "Video",
                    "filepath": video_path,
                    "start": 0,
                    "timeline": "default",
                    "subject": "default",
                }
            ]
        )
    )


def _build_events(
    video_path: str,
    *,
    include_text: bool,
    include_audio: bool | None = None,
):
    """Build events without the upstream spaCy-heavy AddText round trip.

    WhisperX already supplies sentence and sequence information. A cumulative
    context is sufficient for TRIBE's contextualized Word extractor and avoids
    downloading ``en_core_web_lg`` during the first request.
    """

    from neuralset.events.transforms import (
        AddConcatenationContext,
        ChunkEvents,
        ExtractAudioFromVideo,
        RemoveMissing,
    )
    from neuralset.events.utils import standardize_events

    events = _base_video_events(video_path)
    warnings: list[str] = []
    if include_audio is None:
        include_audio = "audio" in INFERENCE_MODALITIES or include_text
    if include_audio:
        try:
            events = ExtractAudioFromVideo()(events)
        except Exception as exc:
            warnings.append(f"audio extraction skipped: {type(exc).__name__}: {exc}")

    for event_type in ("Audio", "Video"):
        try:
            events = ChunkEvents(
                event_type_to_chunk=event_type,
                max_duration=60,
                min_duration=30,
            )(events)
        except Exception as exc:
            warnings.append(
                f"{event_type.lower()} chunking skipped: "
                f"{type(exc).__name__}: {exc}"
            )

    if include_text and "Audio" in set(events["type"]):
        from tribev2.eventstransforms import ExtractWordsFromAudio

        events = ExtractWordsFromAudio()(events)
        if "Word" in set(events["type"]):
            events = AddConcatenationContext(
                sentence_only=False,
                max_context_len=1024,
                split_field="",
            )(events)
            events = RemoveMissing()(events)

    return standardize_events(events), warnings


def _drop_text_events(events):
    if "type" not in events.columns:
        return events
    return events.loc[
        ~events["type"].isin({"Word", "Text", "Sentence"})
    ].reset_index(drop=True)


def _predict_video(model: Any, video_path: str) -> tuple[np.ndarray, dict[str, Any]]:
    if TEXT_MODE == "required" and not _state["text_enabled"]:
        raise PipelineFailure(
            "text_preflight",
            RuntimeError(_state["text_reason"]),
        )

    use_text = bool(_state["text_enabled"] and TEXT_MODE != "off")
    warnings: list[str] = []
    try:
        events, event_warnings = _build_events(
            video_path,
            include_text=use_text,
            include_audio="audio" in INFERENCE_MODALITIES or use_text,
        )
        warnings.extend(event_warnings)
    except Exception as exc:
        if TEXT_MODE == "required":
            raise PipelineFailure("event_preprocessing", exc) from exc
        warnings.append(
            f"text preprocessing failed; retrying "
            f"{'+'.join(INFERENCE_MODALITIES)}: "
            f"{type(exc).__name__}: {exc}"
        )
        _release_transient_memory()
        try:
            events, event_warnings = _build_events(
                video_path,
                include_text=False,
                include_audio="audio" in INFERENCE_MODALITIES,
            )
            warnings.extend(event_warnings)
            use_text = False
        except Exception as fallback_exc:
            raise PipelineFailure(
                "event_preprocessing",
                exc,
                fallback_exc=fallback_exc,
            ) from fallback_exc

    try:
        predictions, _ = model.predict(events, verbose=False)
    except Exception as exc:
        has_text_events = (
            "type" in events.columns
            and events["type"].isin({"Word", "Text", "Sentence"}).any()
        )
        if TEXT_MODE != "required" and has_text_events:
            warnings.append(
                f"text inference failed; retrying "
                f"{'+'.join(INFERENCE_MODALITIES)}: "
                f"{type(exc).__name__}: {exc}"
            )
            _release_transient_memory()
            fallback_events = _drop_text_events(events)
            try:
                predictions, _ = model.predict(fallback_events, verbose=False)
                events = fallback_events
                use_text = False
            except Exception as fallback_exc:
                raise PipelineFailure(
                    "model_predict",
                    exc,
                    fallback_exc=fallback_exc,
                ) from fallback_exc
        else:
            raise PipelineFailure("model_predict", exc) from exc

    predictions = np.asarray(predictions)
    modalities = sorted(
        {
            {"Video": "video", "Audio": "audio", "Word": "text"}.get(t, "")
            for t in set(events["type"])
        }
        - {""}
    )
    return predictions, {
        "modalities": modalities,
        "text_used": bool(use_text and "text" in modalities),
        "warnings": warnings,
    }


def _run_locked(model: Any, video_path: str):
    with _inference_lock:
        return _predict_video(model, video_path)


def _reward_feedback(
    report: dict[str, Any],
    visual_features: dict[str, Any],
    yolo_features: dict[str, Any],
    ad_score: dict[str, Any],
) -> dict[str, Any]:
    """Turn the weakest weighted ad features into generator directions."""
    regions = report.get("regions", [])
    weakest = min(regions, key=lambda item: item["score"]) if regions else None
    weak_window = report.get("weakWindow", {})
    actions: list[str] = []
    weak_names = {item["name"] for item in ad_score["weakestFeatures"]}

    if weakest and any(name.startswith("tribe_") for name in weak_names):
        lever = {
            "LANG": "make the spoken/on-screen message clearer and more immediate",
            "VIS": "add a readable visual reveal or purposeful camera/subject motion",
            "AUD": "increase audio contrast with a beat, emphasis, or cleaner speech",
            "ATTN": "introduce a salient pattern interrupt or surprising change",
        }.get(weakest["short"], "strengthen the weakest cortical channel")
        actions.append(
            f"At {weak_window.get('startTime', 0)}–"
            f"{weak_window.get('endTime', 0)}s, {lever}."
        )

    sharpness = float(visual_features.get("sharpness", 500.0))
    brightness = float(visual_features.get("brightness", 0.5))
    motion = float(visual_features.get("motion_energy", 0.08))
    scene_rate = float(visual_features.get("scene_change_rate", 0.0))

    if "opencv_sharpness" in weak_names or sharpness < 180:
        actions.append("Increase subject sharpness and reduce blur/compression.")
    if "opencv_exposure" in weak_names and brightness < 0.5:
        actions.append("Raise exposure so the focal subject reads instantly.")
    elif "opencv_exposure" in weak_names and brightness >= 0.5:
        actions.append("Reduce clipped highlights and restore visible detail.")
    if "opencv_motion" in weak_names and motion < 0.08:
        actions.append("Add one controlled motion beat; the clip is visually static.")
    elif "opencv_motion" in weak_names and motion >= 0.08:
        actions.append("Reduce chaotic motion; preserve one clear focal action.")
    if "opencv_pacing" in weak_names or scene_rate > 0.5:
        actions.append("Use fewer cuts inside the three-second window.")
    if yolo_features.get("available"):
        if "yolo_detection_coverage" in weak_names:
            actions.append("Keep a recognizable product/person visible across more frames.")
        if "yolo_focal_size" in weak_names:
            actions.append("Make the primary product or subject occupy more of the frame.")
        if "yolo_centering" in weak_names:
            actions.append("Move the primary product or subject closer to the visual focal area.")
        if "yolo_focal_confidence" in weak_names:
            actions.append("Simplify the composition so the main subject is unambiguous.")
    if not actions:
        actions.append("Preserve the structure and make one small targeted variation.")

    return {
        "reward_metric": "adScore",
        "ad_score": ad_score["adScore"],
        "weakest_ad_features": ad_score["weakestFeatures"],
        "weakest_region": weakest,
        "weak_window": weak_window,
        "next_actions": actions[:3],
        "generator_instruction": " ".join(actions[:3]),
    }


async def _score_upload(
    video: UploadFile,
    model: Any,
    scorer: EngagementScorer,
) -> dict[str, Any]:
    suffix = Path(video.filename or "clip.mp4").suffix.lower() or ".mp4"
    if suffix not in {".mp4", ".avi", ".mkv", ".mov", ".webm"}:
        raise HTTPException(status_code=415, detail=f"unsupported video type: {suffix}")

    raw_path = normalized_path = None
    size = 0
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as raw:
            raw_path = raw.name
            while chunk := await video.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"video exceeds {MAX_UPLOAD_BYTES} bytes",
                    )
                raw.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="empty upload")

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as normalized:
            normalized_path = normalized.name
        try:
            visual_features = await run_in_threadpool(
                normalize_short_video,
                raw_path,
                normalized_path,
                max_seconds=MAX_CLIP_SECONDS,
            )
        except Exception as exc:
            failure = PipelineFailure("opencv_preprocessing", exc)
            payload = failure.payload()
            _state["last_error"] = {
                **payload,
                "traceback": traceback.format_exc(),
            }
            raise HTTPException(status_code=422, detail=payload) from exc

        yolo_features = await run_in_threadpool(
            extract_yolo_features,
            normalized_path,
            model_path=YOLO_MODEL,
        )
        try:
            predictions, inference = await run_in_threadpool(
                _run_locked,
                model,
                normalized_path,
            )
            report = scorer.score(predictions)
        except PipelineFailure as exc:
            payload = exc.payload()
            _state["last_error"] = {
                **payload,
                "traceback": traceback.format_exc(),
            }
            print(f"[neuro] request failed:\n{traceback.format_exc()}", flush=True)
            raise HTTPException(status_code=500, detail=payload) from exc
        except Exception as exc:
            failure = PipelineFailure("engagement_scoring", exc)
            payload = failure.payload()
            _state["last_error"] = {
                **payload,
                "traceback": traceback.format_exc(),
            }
            print(f"[neuro] scoring failed:\n{traceback.format_exc()}", flush=True)
            raise HTTPException(status_code=500, detail=payload) from exc

        ad_score = build_ad_score(report, visual_features, yolo_features)
        report["adScore"] = ad_score["adScore"]
        # Backward-compatible alias for existing clients/skills.
        report["engagementScore"] = ad_score["adScore"]
        report["adScoreBreakdown"] = ad_score
        report["video_name"] = video.filename
        report["inference"] = inference
        report["videoFeatures"] = visual_features
        report["yoloFeatures"] = yolo_features
        report["rewardFeedback"] = _reward_feedback(
            report,
            visual_features,
            yolo_features,
            ad_score,
        )
        _state["last_error"] = None
        return report
    finally:
        await video.close()
        for path in (raw_path, normalized_path):
            if path:
                Path(path).unlink(missing_ok=True)


@app.post("/score")
async def score(
    video: UploadFile = File(...),
    x_api_key: str | None = Header(default=None),
):
    _check_key(x_api_key)
    model, scorer = _state["model"], _state["scorer"]
    if model is None or scorer is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "model_not_ready",
                "startup_error": _state["startup_error"],
            },
        )

    return await _score_upload(video, model, scorer)


@app.post("/train-loop")
async def train_loop(
    videos: list[UploadFile] = File(...),
    max_iterations: int = Form(default=MAX_LOOP_ITERATIONS),
    epsilon: float = Form(default=0.5),
    x_api_key: str | None = Header(default=None),
):
    """Score up to five <=3s candidates and keep the best non-regressing result.

    This is the evaluation/reward side of the optimization loop. Pika can
    generate one candidate per iteration, then submit the candidates here in
    order. The endpoint never accepts more than five iterations.
    """
    _check_key(x_api_key)
    model, scorer = _state["model"], _state["scorer"]
    if model is None or scorer is None:
        raise HTTPException(status_code=503, detail="model not ready")
    if not 1 <= max_iterations <= MAX_LOOP_ITERATIONS:
        raise HTTPException(
            status_code=422,
            detail=f"max_iterations must be between 1 and {MAX_LOOP_ITERATIONS}",
        )
    if not videos:
        raise HTTPException(status_code=422, detail="at least one candidate is required")
    if len(videos) > max_iterations:
        raise HTTPException(
            status_code=422,
            detail=(
                f"received {len(videos)} candidates but max_iterations="
                f"{max_iterations}"
            ),
        )

    history = []
    best_iteration = None
    best_score = float("-inf")
    no_improvement = 0
    stop_reason = "candidate_budget_exhausted"

    for index, video in enumerate(videos, start=1):
        report = await _score_upload(video, model, scorer)
        score_value = float(report["adScore"])
        prior_best = None if best_iteration is None else best_score
        reward = 0.0 if prior_best is None else round(score_value - prior_best, 1)
        accepted = best_iteration is None or score_value > best_score
        if accepted:
            best_score = score_value
            best_iteration = index
        if prior_best is not None and reward < epsilon:
            no_improvement += 1
        else:
            no_improvement = 0
        history.append(
            {
                "iteration": index,
                "candidate": report["video_name"],
                "score": score_value,
                "ad_score": score_value,
                "reward": reward,
                "best_score_before": prior_best,
                "accepted": accepted,
                "activation_score": report["activationScore"],
                "visual_score": report["videoFeatures"]["visual_score"],
                "weak_window": report["weakWindow"],
                "trimmed_to_seconds": report["videoFeatures"]["duration"],
                "next_action": report["rewardFeedback"]["generator_instruction"],
                "report": report,
            }
        )
        if no_improvement >= 2:
            stop_reason = f"plateau: reward < {epsilon} for two iterations"
            break

    return {
        "status": "completed",
        "iterations_run": len(history),
        "max_iterations": max_iterations,
        "max_video_seconds": MAX_CLIP_SECONDS,
        "best_iteration": best_iteration,
        "best_score": round(best_score, 1),
        "stop_reason": stop_reason,
        "history": history,
    }
