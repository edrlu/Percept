from __future__ import annotations

import subprocess

import cv2
import numpy as np

from video_features import extract_opencv_features, normalize_short_video


def make_test_video(path, *, seconds=4.0, fps=12):
    width, height = 160, 96
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    assert writer.isOpened()
    for index in range(int(seconds * fps)):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        x = (index * 5) % (width - 30)
        cv2.rectangle(frame, (x, 25), (x + 30, 60), (40, 180, 240), -1)
        writer.write(frame)
    writer.release()


def test_opencv_features_decode_real_video(tmp_path):
    source = tmp_path / "source.mp4"
    make_test_video(source)
    features = extract_opencv_features(source, max_seconds=3.0)

    assert features["sampled_frames"] > 0
    assert features["motion_energy"] > 0
    assert 0 <= features["visual_score"] <= 100


def test_normalization_trims_to_three_seconds(tmp_path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "normalized.mp4"
    make_test_video(source, seconds=4.0)

    features = normalize_short_video(source, output)

    assert output.exists()
    assert features["trimmed"] is True
    assert features["duration"] <= 3.05
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert float(probe.stdout.strip()) <= 3.05
