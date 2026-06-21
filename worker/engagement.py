"""Shared TRIBE v2 engagement analysis.

Pure (FastAPI-free) helpers used by BOTH the web worker (`app.py`) and the
MCP server (`mcp_server.py`):

- the four cortical surface-proxy "engagement families",
- per-vertex family assignment over the fsaverage5 mesh,
- `build_engagement`, which summarises a predicted surface-response tensor into
  a compact, browser/agent-friendly timeline, and
- `compute_peaks`, which turns the global engagement trace into ranked,
  trim-ready time ranges for the auto-edit plugin.

Scientific scope (carried over from the project README): TRIBE v2 predicts
population-average cortical responses. These four regions are manually defined
display proxies — not measurements of emotion, reward, intent, or any
individual viewer's mental state.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import numpy as np

# Docker supplies /data/cache. For a native local run, keep the downloaded
# nilearn surface data beside the worker instead of assuming a writable /data.
CACHE_DIR = os.getenv("TRIBEV2_CACHE_DIR", str(Path(__file__).resolve().parent / ".cache"))

# Cortical surface proxy regions. TRIBE v2 predicts average-subject responses on
# the fsaverage5 cortical mesh; these manually defined regions are display
# summaries only. Each entry: key, display label, short tag, side-panel colour,
# and a centre (|x| laterality, y anterior, z superior, radius) in FreeSurfer
# surface mm.
ENGAGEMENT_FAMILIES = [
    {"key": "reward_desire",      "name": "Ventromedial PFC proxy", "short": "vmPFC", "color": "#ffb13b", "centroid": (15.0, 56.0, -10.0, 28.0)},
    {"key": "emotional_response", "name": "Anterior temporal proxy", "short": "aTEMP", "color": "#ff5a7a", "centroid": (46.0, 12.0, -20.0, 30.0)},
    {"key": "personal_relevance", "name": "Lateral PFC proxy",       "short": "lPFC",  "color": "#9b8cff", "centroid": (40.0, 30.0, 40.0, 28.0)},
    {"key": "memory_encoding",    "name": "Ventral temporal proxy",  "short": "vTEMP", "color": "#3fd6c0", "centroid": (47.0, -42.0, -10.0, 28.0)},
]
_FAMILY_CENTROIDS = [f["centroid"] for f in ENGAGEMENT_FAMILIES]
_KEYS = [f["key"] for f in ENGAGEMENT_FAMILIES]


def _assign_families(coords: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Label each vertex with the nearest cognitive family and a 0..1 falloff
    weight (1 at the territory centre -> 0 at its edge)."""
    x, y, z = coords[:, 0], coords[:, 1], coords[:, 2]
    best = np.full(coords.shape[0], -1, dtype=np.int8)
    best_dist = np.full(coords.shape[0], np.inf, dtype=np.float32)
    for family, (mag, cy, cz, radius) in enumerate(_FAMILY_CENTROIDS):
        cx = np.sign(x + 1e-6) * mag  # lateralise the centre to each hemisphere
        dist = np.sqrt((x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2) / radius
        closer = dist < np.minimum(best_dist, 1.0)
        best[closer] = family
        best_dist[closer] = dist[closer]
    weight = np.clip(1.0 - best_dist, 0.0, 1.0).astype(np.float32)
    return best, weight


@lru_cache(maxsize=1)
def family_data_fs5() -> tuple[np.ndarray, np.ndarray]:
    """Per-vertex family + falloff weight over fsaverage5 (left then right)."""
    from nilearn.datasets import fetch_surf_fsaverage
    from nilearn.surface import load_surf_mesh

    fsaverage = fetch_surf_fsaverage(mesh="fsaverage5", data_dir=str(Path(CACHE_DIR) / "nilearn"))
    left, _ = load_surf_mesh(fsaverage.pial_left)
    right, _ = load_surf_mesh(fsaverage.pial_right)
    fam_l, w_l = _assign_families(left)
    fam_r, w_r = _assign_families(right)
    return np.concatenate([fam_l, fam_r]), np.concatenate([w_l, w_r])


def compute_peaks(
    global_trace: np.ndarray,
    traces_norm: np.ndarray,
    tr: float,
    top_peaks: int = 5,
    window_s: float = 4.0,
) -> list[dict]:
    """Greedily pick the top non-overlapping engagement peaks and return them as
    trim-ready time ranges.

    Each peak: rank, centre time, a [start_s, end_s] window for an editor to cut,
    the dominant engagement dimension at that moment, and the global score.

    Args:
        global_trace: per-frame overall engagement (0..100).
        traces_norm: (n_families, n_frames) per-dimension engagement, key order.
        tr: seconds per frame (TRIBE v2's repetition time).
        top_peaks: how many peaks to return.
        window_s: total clip width centred on each peak.
    """
    g = np.asarray(global_trace, dtype=np.float64)
    n = g.shape[0]
    if n == 0:
        return []
    duration = max(n * tr, tr)
    win = float(min(window_s, duration)) if duration > 0 else window_s
    half_frames = max(1, int(round((win / 2.0) / tr)))

    work = g.copy()
    peaks: list[dict] = []
    for rank in range(1, max(1, top_peaks) + 1):
        i = int(np.argmax(work))
        if not np.isfinite(work[i]):
            break  # everything already suppressed
        center = round(i * tr, 2)
        start = round(max(0.0, center - win / 2.0), 2)
        end = round(min(duration, center + win / 2.0), 2)
        if traces_norm.size:
            dim_idx = int(np.argmax(traces_norm[:, i]))
            family = ENGAGEMENT_FAMILIES[dim_idx]
        else:
            family = {"key": "global", "short": "CORTEX", "name": "Cortex"}
        peaks.append({
            "rank": rank,
            "center_s": center,
            "start_s": start,
            "end_s": end,
            "score": round(float(g[i]), 1),
            "dimension": family["key"],
            "label": family["short"],
            "name": family["name"],
        })
        lo, hi = max(0, i - half_frames), min(n, i + half_frames + 1)
        work[lo:hi] = -np.inf  # suppress so the next peak doesn't overlap
    return peaks


def build_engagement(predictions: np.ndarray, tr: float, top_peaks: int = 5) -> dict:
    """Summarise a predicted surface-response tensor into a compact timeline.

    Args:
        predictions: (n_frames, n_vertices) predicted fsaverage5 surface response.
        tr: seconds per frame (e.g. ``float(model.data.TR)``).
        top_peaks: number of peak ranges to surface for auto-editing.
    """
    # Standardize globally for visualization; raw values stay on the model side.
    mean = predictions.mean()
    std = predictions.std() or 1.0
    z = np.abs((predictions - mean) / std)
    global_trace = np.clip(z.mean(axis=1) * 28 + 18, 0, 100)

    # Per-engagement-system response over time, grouped by the cortical
    # territories the viewer lights up. Normalised jointly so the dominant
    # system reads brightest.
    families, _ = family_data_fs5()
    traces = np.zeros((len(_KEYS), z.shape[0]), dtype=np.float32)
    for i in range(len(_KEYS)):
        mask = families == i
        if mask.any():
            traces[i] = z[:, mask].mean(axis=1)
    scale = float(traces.max()) or 1.0
    norm = np.round(traces / scale * 100, 1)

    regions = []
    cognitive_series = {}
    for i, fam in enumerate(ENGAGEMENT_FAMILIES):
        values = norm[i].tolist()
        cognitive_series[fam["key"]] = values
        regions.append({
            "name": fam["name"],
            "short": fam["short"],
            "color": fam["color"],
            "score": round(float(norm[i].max()), 1),
            "values": values,
        })
    regions.sort(key=lambda r: r["score"], reverse=True)

    peaks = compute_peaks(global_trace, norm, tr, top_peaks=top_peaks)
    peak_index = int(np.argmax(global_trace))
    peak_label = regions[0]["short"] if regions else "CORTEX"
    return {
        "duration": round(len(global_trace) * tr, 2),
        "frames": int(len(global_trace)),
        "tr": round(float(tr), 4),
        "source": "model",
        "global": np.round(global_trace, 2).tolist(),
        "regions": regions,
        "cognitiveSeries": cognitive_series,
        "peaks": peaks,
        "peak": {
            "time": round(peak_index * tr, 2),
            "label": peak_label,
            "value": round(float(global_trace[peak_index]), 2),
        },
    }
