from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import server


class FakeModel:
    def __init__(self, *, fail_first: bool = False):
        self.calls = []
        self.fail_first = fail_first

    def predict(self, events, verbose=False):
        self.calls.append(events.copy())
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("401 gated repo meta-llama/Llama-3.2-3B")
        return np.zeros((4, 20_484), dtype=np.float32), []


def events(*types: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "type": list(types),
            "timeline": ["default"] * len(types),
            "start": np.arange(len(types), dtype=float),
        }
    )


def test_event_preprocessing_failure_falls_back_to_av(monkeypatch):
    model = FakeModel()
    server.TEXT_MODE = "auto"
    server.INFERENCE_MODALITIES = ("video", "audio")
    server._state["text_enabled"] = True

    def build(_path, *, include_text, include_audio):
        if include_text:
            raise RuntimeError("uvx whisperx failed")
        assert include_audio is True
        return events("Video", "Audio"), []

    monkeypatch.setattr(server, "_build_events", build)
    predictions, info = server._predict_video(model, "clip.mp4")

    assert predictions.shape == (4, 20_484)
    assert info["modalities"] == ["audio", "video"]
    assert info["text_used"] is False
    assert "retrying video+audio" in info["warnings"][0]


def test_text_model_failure_retries_without_text(monkeypatch):
    model = FakeModel(fail_first=True)
    server.TEXT_MODE = "auto"
    server.INFERENCE_MODALITIES = ("video", "audio")
    server._state["text_enabled"] = True
    monkeypatch.setattr(
        server,
        "_build_events",
        lambda _path, *, include_text, include_audio: (
            events("Video", "Audio", "Word") if include_text else events("Video", "Audio"),
            [],
        ),
    )

    predictions, info = server._predict_video(model, "clip.mp4")

    assert predictions.shape == (4, 20_484)
    assert len(model.calls) == 2
    assert "Word" in set(model.calls[0]["type"])
    assert "Word" not in set(model.calls[1]["type"])
    assert info["modalities"] == ["audio", "video"]
    assert info["text_used"] is False
    assert "text inference failed" in info["warnings"][0]


def test_video_only_mode_does_not_require_audio(monkeypatch):
    model = FakeModel()
    server.TEXT_MODE = "off"
    server.INFERENCE_MODALITIES = ("video",)
    server._state["text_enabled"] = False

    def build(_path, *, include_text, include_audio):
        assert include_text is False
        assert include_audio is False
        return events("Video"), []

    monkeypatch.setattr(server, "_build_events", build)
    predictions, info = server._predict_video(model, "clip.mp4")

    assert predictions.shape == (4, 20_484)
    assert info["modalities"] == ["video"]
    assert info["text_used"] is False


def test_required_text_mode_rejects_missing_access():
    server.TEXT_MODE = "required"
    server._state["text_enabled"] = False
    server._state["text_reason"] = "HF_TOKEN lacks gated access"

    with pytest.raises(server.PipelineFailure) as caught:
        server._predict_video(FakeModel(), "clip.mp4")

    payload = caught.value.payload()
    assert payload["stage"] == "text_preflight"
    assert "HF_TOKEN" in payload["message"]
    assert "Llama-3.2-3B" in payload["hint"]


def test_failure_payload_includes_fallback_error():
    failure = server.PipelineFailure(
        "model_predict",
        RuntimeError("CUDA out of memory"),
        fallback_exc=RuntimeError("ffmpeg decode failed"),
    )
    payload = failure.payload()

    assert payload["stage"] == "model_predict"
    assert "memory pressure" in payload["hint"]
    assert "Re-encode" in payload["fallback"]["hint"]


def test_drop_text_events_keeps_av_rows():
    result = server._drop_text_events(
        events("Video", "Word", "Audio", "Text", "Sentence")
    )
    assert result["type"].tolist() == ["Video", "Audio"]


class FakeScorer:
    def score(self, predictions):
        assert predictions.shape == (4, 20_484)
        return {
            "activationScore": 60.0,
            "duration": 4.0,
            "regions": [],
            "global": [50.0] * 4,
            "peak": {},
            "weakWindow": {},
        }


def test_score_endpoint_returns_inference_metadata(monkeypatch):
    server._state["model"] = FakeModel()
    server._state["scorer"] = FakeScorer()
    monkeypatch.setattr(
        server,
        "_run_locked",
        lambda _model, _path: (
            np.zeros((4, 20_484), dtype=np.float32),
            {
                "modalities": ["audio", "video"],
                "text_used": False,
                "warnings": ["text unavailable"],
            },
        ),
    )
    monkeypatch.setattr(
        server,
        "normalize_short_video",
        lambda *_args, **_kwargs: {
            "duration": 3.0,
            "original_duration": 4.0,
            "trimmed": True,
            "brightness": 0.5,
            "contrast": 0.2,
            "sharpness": 600.0,
            "motion_energy": 0.08,
            "scene_change_rate": 0.1,
            "visual_score": 70.0,
        },
    )
    monkeypatch.setattr(
        server,
        "extract_yolo_features",
        lambda *_args, **_kwargs: {
            "available": True,
            "detection_coverage": 1.0,
            "focal_area_ratio": 0.28,
            "focal_confidence": 0.9,
            "focal_centering": 0.9,
            "classes": ["person"],
        },
    )

    client = TestClient(server.app)
    response = client.post(
        "/score",
        files={"video": ("clip.mp4", b"fake-video", "video/mp4")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["video_name"] == "clip.mp4"
    assert payload["inference"]["modalities"] == ["audio", "video"]
    assert payload["inference"]["text_used"] is False
    assert payload["adScore"] == payload["engagementScore"]
    assert len(payload["adScoreBreakdown"]["features"]) == 13
    assert payload["rewardFeedback"]["reward_metric"] == "adScore"


def test_score_endpoint_returns_structured_failure(monkeypatch):
    server._state["model"] = FakeModel()
    server._state["scorer"] = FakeScorer()

    def fail(_model, _path):
        raise server.PipelineFailure(
            "event_preprocessing",
            RuntimeError("uvx whisperx failed"),
        )

    monkeypatch.setattr(server, "_run_locked", fail)
    monkeypatch.setattr(
        server,
        "normalize_short_video",
        lambda *_args, **_kwargs: {
            "duration": 3.0,
            "original_duration": 3.0,
            "trimmed": False,
            "visual_score": 50.0,
        },
    )
    client = TestClient(server.app)
    response = client.post(
        "/score",
        files={"video": ("clip.mp4", b"fake-video", "video/mp4")},
    )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["stage"] == "event_preprocessing"
    assert "WhisperX" in detail["hint"]


def test_train_loop_emits_rewards_and_keeps_best(monkeypatch):
    server._state["model"] = FakeModel()
    server._state["scorer"] = FakeScorer()
    scores = {"a.mp4": 50.0, "b.mp4": 55.0, "c.mp4": 54.0}

    async def fake_score(upload, _model, _scorer):
        score = scores[upload.filename]
        await upload.close()
        return {
            "video_name": upload.filename,
            "adScore": score,
            "engagementScore": score,
            "activationScore": score - 1,
            "videoFeatures": {"visual_score": score + 4, "duration": 3.0},
            "weakWindow": {"startTime": 0.0, "endTime": 1.0},
            "rewardFeedback": {"generator_instruction": f"improve {upload.filename}"},
        }

    monkeypatch.setattr(server, "_score_upload", fake_score)
    client = TestClient(server.app)
    response = client.post(
        "/train-loop",
        data={"max_iterations": "3", "epsilon": "0.5"},
        files=[
            ("videos", ("a.mp4", b"a", "video/mp4")),
            ("videos", ("b.mp4", b"b", "video/mp4")),
            ("videos", ("c.mp4", b"c", "video/mp4")),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["best_iteration"] == 2
    assert payload["best_score"] == 55.0
    assert [item["reward"] for item in payload["history"]] == [0.0, 5.0, -1.0]
    assert payload["history"][2]["accepted"] is False


def test_train_loop_rejects_more_than_five_iterations():
    server._state["model"] = FakeModel()
    server._state["scorer"] = FakeScorer()
    client = TestClient(server.app)
    response = client.post(
        "/train-loop",
        data={"max_iterations": "6"},
        files=[("videos", ("a.mp4", b"a", "video/mp4"))],
    )
    assert response.status_code == 422
    assert "between 1 and 5" in response.json()["detail"]
