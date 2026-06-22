"""Auditable linear ad-score over TRIBE, OpenCV, and YOLO features."""

from __future__ import annotations

from typing import Any

import numpy as np


FEATURE_WEIGHTS = {
    # TRIBE cortical activation features: 55%
    "tribe_attention": 0.18,
    "tribe_visual": 0.14,
    "tribe_language": 0.13,
    "tribe_auditory": 0.10,
    # OpenCV production features: 30%
    "opencv_exposure": 0.06,
    "opencv_contrast": 0.05,
    "opencv_sharpness": 0.07,
    "opencv_motion": 0.08,
    "opencv_pacing": 0.04,
    # YOLO semantic/composition features: 15%
    "yolo_detection_coverage": 0.04,
    "yolo_focal_size": 0.04,
    "yolo_focal_confidence": 0.03,
    "yolo_centering": 0.04,
}


def _clamp(value: float) -> float:
    return float(np.clip(value, 0.0, 100.0))


def _target_score(value: float, target: float, tolerance: float) -> float:
    return _clamp(100.0 - abs(value - target) / tolerance * 100.0)


def _tribe_region_scores(report: dict[str, Any]) -> dict[str, float]:
    by_short = {
        region["short"]: float(region["score"])
        for region in report.get("regions", [])
    }
    return {
        "tribe_attention": by_short.get("ATTN"),
        "tribe_visual": by_short.get("VIS"),
        "tribe_language": by_short.get("LANG"),
        "tribe_auditory": by_short.get("AUD"),
    }


def _optional_feature(
    source: str,
    raw_value: Any,
    score_fn,
    *,
    reason: str,
) -> dict[str, Any]:
    if raw_value is None:
        return {
            "source": source,
            "raw_value": None,
            "score": None,
            "unavailable_reason": reason,
        }
    return {
        "source": source,
        "raw_value": raw_value,
        "score": _clamp(float(score_fn(float(raw_value)))),
    }


def build_ad_score(
    tribe_report: dict[str, Any],
    opencv: dict[str, Any],
    yolo: dict[str, Any],
) -> dict[str, Any]:
    """Return raw values, normalized scores, effective weights, contributions."""
    values: dict[str, dict[str, Any]] = {}

    for key, score in _tribe_region_scores(tribe_report).items():
        values[key] = {
            "source": "tribev2",
            "raw_value": score,
            "score": None if score is None else _clamp(score),
        }

    values.update(
        {
            "opencv_exposure": _optional_feature(
                "opencv",
                opencv.get("brightness"),
                lambda value: _target_score(value, 0.5, 0.5),
                reason="brightness unavailable",
            ),
            "opencv_contrast": _optional_feature(
                "opencv",
                opencv.get("contrast"),
                lambda value: value / 0.24 * 100.0,
                reason="contrast unavailable",
            ),
            "opencv_sharpness": _optional_feature(
                "opencv",
                opencv.get("sharpness"),
                lambda value: value / 850.0 * 100.0,
                reason="sharpness unavailable",
            ),
            "opencv_motion": _optional_feature(
                "opencv",
                opencv.get("motion_energy"),
                lambda value: _target_score(value, 0.08, 0.08),
                reason="motion energy unavailable",
            ),
            "opencv_pacing": _optional_feature(
                "opencv",
                opencv.get("scene_change_rate"),
                lambda value: 100.0 - max(0.0, value - 0.34) * 150,
                reason="scene-change rate unavailable",
            ),
        }
    )

    if yolo.get("available"):
        values.update(
            {
                "yolo_detection_coverage": {
                    "source": "yolo",
                    "raw_value": yolo["detection_coverage"],
                    "score": _clamp(float(yolo["detection_coverage"]) * 100),
                },
                "yolo_focal_size": {
                    "source": "yolo",
                    "raw_value": yolo["focal_area_ratio"],
                    "score": _target_score(float(yolo["focal_area_ratio"]), 0.28, 0.28),
                },
                "yolo_focal_confidence": {
                    "source": "yolo",
                    "raw_value": yolo["focal_confidence"],
                    "score": _clamp(float(yolo["focal_confidence"]) * 100),
                },
                "yolo_centering": {
                    "source": "yolo",
                    "raw_value": yolo["focal_centering"],
                    "score": _clamp(float(yolo["focal_centering"]) * 100),
                },
            }
        )
    else:
        for key in (
            "yolo_detection_coverage",
            "yolo_focal_size",
            "yolo_focal_confidence",
            "yolo_centering",
        ):
            values[key] = {
                "source": "yolo",
                "raw_value": None,
                "score": None,
                "unavailable_reason": yolo.get("reason", "YOLO unavailable"),
            }

    available_weight = sum(
        FEATURE_WEIGHTS[key]
        for key, feature in values.items()
        if feature["score"] is not None
    )
    if available_weight <= 0:
        raise ValueError("No ad-score features are available")

    feature_rows = []
    source_totals: dict[str, float] = {}
    total = 0.0
    for key, base_weight in FEATURE_WEIGHTS.items():
        feature = values[key]
        effective_weight = (
            base_weight / available_weight if feature["score"] is not None else 0.0
        )
        contribution = (
            float(feature["score"]) * effective_weight
            if feature["score"] is not None
            else 0.0
        )
        source_totals[feature["source"]] = (
            source_totals.get(feature["source"], 0.0) + contribution
        )
        total += contribution
        feature_rows.append(
            {
                "name": key,
                **feature,
                "base_weight": base_weight,
                "effective_weight": round(effective_weight, 6),
                "contribution": round(contribution, 3),
            }
        )

    weakest = sorted(
        (row for row in feature_rows if row["score"] is not None),
        key=lambda row: row["score"],
    )[:3]
    strongest = sorted(
        (row for row in feature_rows if row["score"] is not None),
        key=lambda row: row["contribution"],
        reverse=True,
    )[:3]
    return {
        "adScore": round(total, 1),
        "formula": "sum(normalized_feature_score * effective_weight)",
        "weights_redistributed": available_weight < 0.999999,
        "features": feature_rows,
        "sourceContributions": {
            key: round(value, 3) for key, value in source_totals.items()
        },
        "weakestFeatures": [
            {"name": row["name"], "score": round(float(row["score"]), 1)}
            for row in weakest
        ],
        "strongestContributors": [
            {"name": row["name"], "contribution": row["contribution"]}
            for row in strongest
        ],
    }
