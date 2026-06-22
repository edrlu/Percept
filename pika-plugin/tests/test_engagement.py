from __future__ import annotations

import numpy as np
import pytest

from engagement import EngagementScorer


def scorer_with_fake_atlas() -> EngagementScorer:
    scorer = EngagementScorer(cache_dir="/tmp/unused-atlas", tr_seconds=1.0)
    scorer._families = [
        {"roi": np.array([0, 1, 2])},
        {"roi": np.array([3, 4, 5])},
        {"roi": np.array([6, 7, 8])},
        {"roi": np.array([9, 10, 11])},
    ]
    return scorer


def test_score_returns_complete_report():
    rng = np.random.default_rng(7)
    predictions = rng.normal(size=(8, 20_484))
    report = scorer_with_fake_atlas().score(predictions)

    assert report["frames"] == 8
    assert report["duration"] == 8.0
    assert len(report["global"]) == 8
    assert len(report["regions"]) == 4
    assert report["weakWindow"]["endTime"] > report["weakWindow"]["startTime"]
    assert 0 <= report["activationScore"] <= 100


def test_activation_score_is_not_forced_to_fifty():
    predictions = np.zeros((4, 20_484), dtype=np.float64)
    predictions[:, :12] = 4.0
    report = scorer_with_fake_atlas().score(predictions)
    assert report["activationScore"] > 50


@pytest.mark.parametrize(
    "predictions, message",
    [
        (np.zeros(20_484), "2D"),
        (np.zeros((2, 100)), "20484"),
        (np.zeros((0, 20_484)), "zero prediction"),
        (np.full((2, 20_484), np.nan), "NaN"),
    ],
)
def test_score_rejects_invalid_predictions(predictions, message):
    with pytest.raises(ValueError, match=message):
        scorer_with_fake_atlas().score(predictions)
