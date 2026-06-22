"""Deterministic short-video normalization and OpenCV feature extraction."""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np

MAX_CLIP_SECONDS = 3.0
DEFAULT_YOLO_MODEL = "yolov8n.pt"


def normalize_short_video(
    source: str | Path,
    destination: str | Path,
    *,
    max_seconds: float = MAX_CLIP_SECONDS,
) -> dict:
    """Transcode to a browser/model-safe MP4 and trim to ``max_seconds``."""
    source, destination = Path(source), Path(destination)
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required to normalize uploaded videos")

    original = extract_opencv_features(source, max_seconds=None)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-t",
        f"{max_seconds:.3f}",
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0 or not destination.exists():
        raise RuntimeError(f"ffmpeg normalization failed: {result.stderr.strip()}")

    normalized = extract_opencv_features(destination, max_seconds=max_seconds)
    normalized["original_duration"] = original["duration"]
    normalized["trimmed"] = original["duration"] > max_seconds + 0.05
    return normalized


def extract_opencv_features(
    video_path: str | Path,
    *,
    max_seconds: float | None = MAX_CLIP_SECONDS,
    max_frames: int = 90,
) -> dict:
    """Read a short clip with OpenCV and return interpretable visual features."""
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError("OpenCV could not open the uploaded video")

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if fps <= 0 or width <= 0 or height <= 0:
        capture.release()
        raise ValueError("Video metadata is invalid (fps or dimensions are zero)")

    metadata_duration = frame_count / fps if frame_count > 0 else 0.0
    frame_limit = max_frames
    if max_seconds is not None:
        frame_limit = min(frame_limit, max(1, int(math.ceil(fps * max_seconds))))

    brightness, contrast, saturation, sharpness = [], [], [], []
    motion, histogram_deltas = [], []
    previous_gray = previous_hist = None
    decoded = 0

    while decoded < frame_limit:
        ok, frame = capture.read()
        if not ok:
            break
        decoded += 1
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        brightness.append(float(gray.mean() / 255.0))
        contrast.append(float(gray.std() / 255.0))
        saturation.append(float(hsv[..., 1].mean() / 255.0))
        sharpness.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))

        if previous_gray is not None:
            motion.append(float(cv2.absdiff(gray, previous_gray).mean() / 255.0))
        hist = cv2.calcHist([gray], [0], None, [32], [0, 256])
        cv2.normalize(hist, hist)
        if previous_hist is not None:
            correlation = cv2.compareHist(previous_hist, hist, cv2.HISTCMP_CORREL)
            histogram_deltas.append(float(np.clip(1.0 - correlation, 0.0, 2.0) / 2.0))
        previous_gray, previous_hist = gray, hist

    capture.release()
    if decoded == 0:
        raise ValueError("OpenCV decoded zero frames")

    duration = metadata_duration or decoded / fps
    if max_seconds is not None:
        duration = min(duration, max_seconds)

    values = {
        "duration": round(float(duration), 3),
        "fps": round(fps, 3),
        "width": width,
        "height": height,
        "sampled_frames": decoded,
        "brightness": round(float(np.mean(brightness)), 4),
        "contrast": round(float(np.mean(contrast)), 4),
        "saturation": round(float(np.mean(saturation)), 4),
        "sharpness": round(float(np.mean(sharpness)), 2),
        "motion_energy": round(float(np.mean(motion)) if motion else 0.0, 4),
        "scene_change_rate": round(
            float(np.mean(np.asarray(histogram_deltas) > 0.22))
            if histogram_deltas
            else 0.0,
            4,
        ),
    }
    values["visual_score"] = _visual_score(values)
    return values


_YOLO_CACHE: dict[str, object] = {}


def extract_yolo_features(
    video_path: str | Path,
    *,
    model_path: str = DEFAULT_YOLO_MODEL,
    sample_frames: int = 8,
) -> dict:
    """Extract lightweight semantic/composition features with YOLO.

    The scorer degrades gracefully when Ultralytics or weights are unavailable;
    the weighted ad-score builder redistributes unavailable feature weights.
    """
    try:
        from ultralytics import YOLO
    except Exception as exc:
        return {
            "available": False,
            "reason": f"ultralytics unavailable: {type(exc).__name__}: {exc}",
        }

    try:
        model = _YOLO_CACHE.get(model_path)
        if model is None:
            model = YOLO(model_path)
            _YOLO_CACHE[model_path] = model

        capture = cv2.VideoCapture(str(video_path))
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if total <= 0 or width <= 0 or height <= 0:
            capture.release()
            raise ValueError("invalid video metadata for YOLO")

        indices = np.linspace(0, total - 1, min(sample_frames, total), dtype=int)
        frames = []
        for index in indices:
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(index))
            ok, frame = capture.read()
            if ok:
                frames.append(frame)
        capture.release()
        if not frames:
            raise ValueError("YOLO sampling decoded zero frames")

        results = model.predict(frames, verbose=False, conf=0.25)
        focal_areas, confidences, object_counts, center_scores = [], [], [], []
        detected_frames = 0
        class_names: set[str] = set()
        for result in results:
            boxes = result.boxes
            count = len(boxes) if boxes is not None else 0
            object_counts.append(count)
            if not count:
                focal_areas.append(0.0)
                confidences.append(0.0)
                center_scores.append(0.0)
                continue
            detected_frames += 1
            xyxy = boxes.xyxy.detach().cpu().numpy()
            conf = boxes.conf.detach().cpu().numpy()
            cls = boxes.cls.detach().cpu().numpy().astype(int)
            frame_area = float(width * height)
            areas = (
                (xyxy[:, 2] - xyxy[:, 0]) * (xyxy[:, 3] - xyxy[:, 1]) / frame_area
            )
            best = int(np.argmax(areas * np.maximum(conf, 1e-6)))
            focal_areas.append(float(np.clip(areas[best], 0, 1)))
            confidences.append(float(conf[best]))
            cx = float((xyxy[best, 0] + xyxy[best, 2]) / 2 / width)
            cy = float((xyxy[best, 1] + xyxy[best, 3]) / 2 / height)
            distance = math.sqrt((cx - 0.5) ** 2 + (cy - 0.5) ** 2)
            center_scores.append(float(np.clip(1.0 - distance / 0.7071, 0, 1)))
            names = result.names
            class_names.update(str(names.get(int(c), c)) for c in cls)

        return {
            "available": True,
            "sampled_frames": len(frames),
            "detection_coverage": round(detected_frames / len(frames), 4),
            "mean_object_count": round(float(np.mean(object_counts)), 3),
            "focal_area_ratio": round(float(np.mean(focal_areas)), 4),
            "focal_confidence": round(float(np.mean(confidences)), 4),
            "focal_centering": round(float(np.mean(center_scores)), 4),
            "classes": sorted(class_names),
        }
    except Exception as exc:
        return {
            "available": False,
            "reason": f"YOLO inference failed: {type(exc).__name__}: {exc}",
        }


def _visual_score(features: dict) -> float:
    """A bounded production-quality prior; TRIBE remains the primary signal."""
    brightness = float(features["brightness"])
    contrast = float(features["contrast"])
    saturation = float(features["saturation"])
    sharpness = float(features["sharpness"])
    motion = float(features["motion_energy"])
    scene_rate = float(features["scene_change_rate"])

    exposure = np.clip(100.0 - abs(brightness - 0.5) * 180.0, 0, 100)
    contrast_score = np.clip(contrast / 0.24 * 100.0, 0, 100)
    color_score = np.clip(100.0 - abs(saturation - 0.35) * 150.0, 0, 100)
    sharpness_score = np.clip(sharpness / 850.0 * 100.0, 0, 100)
    # Three-second ads benefit from visible motion, but extreme frame-to-frame
    # churn is usually compression/noise rather than meaningful dynamism.
    motion_score = np.clip(100.0 - abs(motion - 0.08) / 0.08 * 100.0, 0, 100)
    cut_score = np.clip(100.0 - max(0.0, scene_rate - 0.34) * 150.0, 0, 100)

    score = (
        0.20 * exposure
        + 0.15 * contrast_score
        + 0.10 * color_score
        + 0.20 * sharpness_score
        + 0.25 * motion_score
        + 0.10 * cut_score
    )
    return round(float(np.clip(score, 0, 100)), 1)
