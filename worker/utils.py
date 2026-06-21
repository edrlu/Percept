"""Video utilities for the Cerebra worker."""

from __future__ import annotations

import json
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


def video_duration(video_path: str | Path) -> float:
    """Return the container duration of a video, in seconds, via ffprobe."""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"ffprobe could not read duration: {result.stderr.strip()}") from exc


def _has_audio(video_path: str | Path) -> bool:
    cmd = ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
           "stream=index", "-of", "csv=p=0", str(video_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return bool(result.stdout.strip())


def _keep_ranges(remove: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    """Invert a set of removal ranges into the kept (complement) ranges over
    [0, duration]. Overlapping/unsorted removal ranges are merged first."""
    cleaned = sorted((max(0.0, min(s, e)), min(duration, max(s, e)))
                     for s, e in remove if min(s, e) < duration and max(s, e) > 0)
    merged: list[tuple[float, float]] = []
    for s, e in cleaned:
        if e - s < 1e-3:
            continue
        if merged and s <= merged[-1][1] + 1e-3:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in merged:
        if s - cursor > 1e-3:
            keep.append((cursor, s))
        cursor = e
    if duration - cursor > 1e-3:
        keep.append((cursor, duration))
    return keep


def splice_video(
    video_path: str | Path,
    remove_ranges: list[tuple[float, float]],
    out_path: str | Path | None = None,
) -> str:
    """Remove the given time ranges from a video and concatenate what's left.

    `remove_ranges` is a list of (start_s, end_s) windows to drop. The kept
    complement is re-encoded in a single ffmpeg pass using the select/aselect
    filters, so cuts can fall on any frame (not just keyframes) and the output
    plays with a continuous, gap-free timeline.

    Args:
        video_path: path to the source video.
        remove_ranges: windows (in seconds) to cut out.
        out_path: destination .mp4; a temp file is used if omitted.

    Returns the path to the spliced .mp4.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(video_path)

    duration = video_duration(video_path)
    keep = _keep_ranges([tuple(r) for r in remove_ranges], duration)
    if not keep:
        raise ValueError("Removal ranges cover the entire video; nothing to keep.")

    out = Path(out_path) if out_path else Path(tempfile.mkdtemp(prefix="splice-")) / f"{video_path.stem}_spliced.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    select_expr = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in keep)
    cmd = ["ffmpeg", "-y", "-i", str(video_path),
           "-vf", f"select='{select_expr}',setpts=N/FRAME_RATE/TB"]
    if _has_audio(video_path):
        cmd += ["-af", f"aselect='{select_expr}',asetpts=N/SR/TB"]
    else:
        cmd += ["-an"]
    cmd += ["-fps_mode", "vfr", "-movflags", "+faststart", str(out)]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg failed to splice video: {result.stderr.strip()[-800:]}")
    return str(out)


if __name__ == "__main__":
    import sys

    # python utils.py frames <video.mp4> <start_s> <end_s>
    # python utils.py splice <video.mp4> <remove_json>   e.g. '[[2,5],[10,12]]'
    if len(sys.argv) >= 2 and sys.argv[1] == "splice":
        if len(sys.argv) != 4:
            sys.exit("usage: python utils.py splice <video.mp4> '[[start,end],...]'")
        ranges = [tuple(r) for r in json.loads(sys.argv[3])]
        print(splice_video(sys.argv[2], ranges))
    elif len(sys.argv) == 4:
        for path in extract_frames(sys.argv[1], float(sys.argv[2]), float(sys.argv[3])):
            print(path)
    else:
        sys.exit("usage: python utils.py <video.mp4> <start_s> <end_s>\n"
                 "       python utils.py splice <video.mp4> '[[start,end],...]'")
