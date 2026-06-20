"""Video utilities for the Cerebra worker."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


def extract_frames(
    video_path: str | Path,
    start_s: float,
    end_s: float,
    out_dir: str | Path | None = None,
) -> list[str]:
    """Extract a single frame at each of two timestamps from an mp4.

    Uses ffmpeg input-side seeking (`-ss` before `-i`) so extraction is fast even
    on long videos. Returns the two PNG paths, in the order [start_s, end_s].

    Args:
        video_path: path to the source .mp4 (or any ffmpeg-decodable video).
        start_s: first timestamp, in seconds.
        end_s: second timestamp, in seconds.
        out_dir: directory to write the frames into; a temp dir is used if omitted.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(video_path)

    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="frames-"))
    out.mkdir(parents=True, exist_ok=True)

    paths: list[str] = []
    for i, ts in enumerate((start_s, end_s)):
        frame = out / f"{video_path.stem}_f{i}_{ts:g}s.png"
        cmd = ["ffmpeg", "-y", "-ss", f"{ts}", "-i", str(video_path),
               "-frames:v", "1", "-q:v", "2", str(frame)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not frame.exists():
            raise RuntimeError(f"ffmpeg failed to extract frame at {ts}s: {result.stderr.strip()}")
        paths.append(str(frame))
    return paths


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 4:
        sys.exit("usage: python utils.py <video.mp4> <start_s> <end_s>")
    for path in extract_frames(sys.argv[1], float(sys.argv[2]), float(sys.argv[3])):
        print(path)
