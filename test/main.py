#!/usr/bin/env python3
"""Extract one JPEG frame every five seconds from a local MP4 file."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def extract_frames(video: str | Path, output_dir: str | Path, seconds: int) -> None:
    video = Path(video)
    output_dir = Path(output_dir)
    if not video.is_file():
        raise FileNotFoundError(f"MP4 file not found: {video}")
    if video.suffix.lower() != ".mp4":
        raise ValueError(f"Expected an MP4 file, received: {video.name}")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required. Install it, then try again.")
    if seconds <= 0:
        raise ValueError("seconds must be greater than zero")

    # Start fresh every time so no old frames remain in the output folder.
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    output_pattern = output_dir / "frame_%05d.jpg"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(video),
            "-vf",
            f"fps=1/{seconds}",
            "-q:v",
            "2",
            str(output_pattern),
        ],
        check=True,
    )


def main() -> None:
    video = "../downloads/cc2.mp4"
    output_dir = "./frames"
    seconds = 5  # Change this to save one frame every N seconds.

    extract_frames(video, output_dir, seconds)
    print(f"Saved frames to {output_dir}")


if __name__ == "__main__":
    main()
