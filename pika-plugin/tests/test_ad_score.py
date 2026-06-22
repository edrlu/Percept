from __future__ import annotations

from ad_score import FEATURE_WEIGHTS, build_ad_score


def tribe_report():
    return {
        "regions": [
            {"short": "ATTN", "score": 70.0},
            {"short": "VIS", "score": 65.0},
            {"short": "LANG", "score": 60.0},
            {"short": "AUD", "score": 55.0},
        ]
    }


def opencv_features():
    return {
        "brightness": 0.5,
        "contrast": 0.24,
        "sharpness": 850.0,
        "motion_energy": 0.08,
        "scene_change_rate": 0.1,
    }


def yolo_features():
    return {
        "available": True,
        "detection_coverage": 1.0,
        "focal_area_ratio": 0.28,
        "focal_confidence": 0.9,
        "focal_centering": 0.9,
    }


def test_weights_sum_to_one():
    assert abs(sum(FEATURE_WEIGHTS.values()) - 1.0) < 1e-9


def test_ad_score_is_exact_linear_sum():
    report = build_ad_score(tribe_report(), opencv_features(), yolo_features())
    recomputed = sum(
        feature["score"] * feature["effective_weight"]
        for feature in report["features"]
        if feature["score"] is not None
    )
    assert report["adScore"] == round(recomputed, 1)
    assert report["weights_redistributed"] is False


def test_missing_yolo_redistributes_weights():
    report = build_ad_score(
        tribe_report(),
        opencv_features(),
        {"available": False, "reason": "offline"},
    )
    assert report["weights_redistributed"] is True
    effective = sum(feature["effective_weight"] for feature in report["features"])
    assert abs(effective - 1.0) < 1e-5
    assert 0 <= report["adScore"] <= 100
