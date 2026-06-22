"""TRIBE v2 -> engagement score.

Turns the raw cortical-response tensor predicted by `facebook/tribev2`
(`(T, 20484)` over the stacked fsaverage5 surface) into a compact, 0-100
"neuro-engagement" report: an overall score, four cognitively-labelled family
traces, and the peak moment.

This is a standalone port of the scoring path in Cerebra's `worker/app.py` --
the WebGL surface-mesh code is intentionally dropped; the plugin only needs the
number and the per-frame traces. Pure numpy, plus MNE once at startup to build
the Glasser/HCP-MMP atlas (cached to an .npz so later starts need no download).

Design notes:
- Engagement families are unions of named HCP-MMP (Glasser) parcels, chosen
  from the engagement-neuroscience literature, defined on TRIBE v2's *native*
  output space (no hand-placed spheres, no interpolation).
- Each timestep is standardized across the full cortical surface, then selected
  ROI activation is measured relative to the rest of the cortex. This avoids the
  former mathematical failure where temporal centering forced every clip's mean
  score back toward 50. All four families weight equally; `reliability` is an
  honest confidence note but never changes the score.
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

# fsaverage5: 10,242 vertices per hemisphere = TRIBE v2's native resolution.
FS5_VERTICES_PER_HEMI = 10242

# Relative cortical scale: a family mean equal to the cortex mean -> 50, and
# +/-CORTICAL_Z_REF SD from the full-cortex mean -> 100/0.
CORTICAL_Z_REF = 2.5

# Four engagement families on the Glasser atlas. `rois` patterns support a
# trailing '*' (prefix match) or leading '*' (suffix match). `reliability`
# records how well TRIBE itself predicts that territory (paper Sec. 3.2-3.3):
# auditory/language are near the noise ceiling; association areas are well
# predicted; primary visual is weaker, so it is intentionally excluded.
ENGAGEMENT_FAMILIES = [
    {"key": "auditory_engagement", "name": "Auditory / speech-music", "short": "AUD",  "color": "#ffb13b",
     "reliability": "high",   "rois": ["A1", "MBelt", "LBelt", "PBelt", "A4", "A5", "STG*", "STS*"]},
    {"key": "language_message",    "name": "Language / message",      "short": "LANG", "color": "#ff5a7a",
     "reliability": "high",   "rois": ["44", "45", "47l", "IFS*", "IFJ*"]},
    {"key": "attention_salience",  "name": "Attention + salience",    "short": "ATTN", "color": "#9b8cff",
     "reliability": "medium", "rois": ["IPS*", "LIP*", "VIP", "FEF", "6a", "AVI", "MI", "FOP*", "a24pr", "p24pr", "PFm", "PGi", "PGs", "TPOJ*"]},
    {"key": "visual_motion",       "name": "Visual / motion",        "short": "VIS",  "color": "#3fd6c0",
     "reliability": "medium", "rois": ["MT", "MST", "V4t", "FST", "LO*", "V3CD"]},
]

_HCP_LOCK = threading.Lock()


def _build_hcp_labels(cache_dir: Path) -> dict:
    """Replicate `tribev2.utils.get_hcp_labels` WITHOUT its ~1.65 GB MNE *sample*
    dataset. We only need a subjects_dir containing `fsaverage`, so we use the
    small `fetch_fsaverage` (~tens of MB) + the HCP-MMP annotation, apply the
    same fsaverage->fsaverage5 downsample (keep vertices < 10242) and L/R stack.
    Result is cached to an .npz so later starts need no MNE.
    """
    cache_file = cache_dir / "hcp_fsaverage5_labels.npz"
    if cache_file.exists():
        data = np.load(cache_file, allow_pickle=False)
        return {k[4:]: data[k] for k in data.files}  # strip the "roi_" key prefix

    import mne

    fs5 = FS5_VERTICES_PER_HEMI
    subjects_dir = cache_dir / "mne_subjects"
    subjects_dir.mkdir(parents=True, exist_ok=True)
    mne.datasets.fetch_fsaverage(subjects_dir=str(subjects_dir), verbose=False)
    mne.datasets.fetch_hcp_mmp_parcellation(
        subjects_dir=str(subjects_dir), accept=True, verbose=False
    )
    labels = mne.read_labels_from_annot(
        "fsaverage", "HCPMMP1", hemi="both", subjects_dir=str(subjects_dir), verbose=False
    )

    out: dict[str, list] = {}
    for label in labels:
        name = label.name[2:].replace("_ROI", "")  # strip "L_"/"R_" prefix + "_ROI"
        if "-lh" in name:
            offset = 0
        elif "-rh" in name:
            offset = fs5
        else:
            continue
        bare = name.replace("-rh", "").replace("-lh", "")
        if not bare or "?" in bare:  # skip the medial-wall / unknown label
            continue
        verts = np.asarray(label.vertices)
        verts = verts[verts < fs5] + offset  # fsaverage meshes are nested: <fs5 == fsaverage5
        out.setdefault(bare, []).append(verts)
    merged = {k: np.concatenate(vs).astype(np.int64) for k, vs in out.items()}

    cache_dir.mkdir(parents=True, exist_ok=True)
    np.savez(cache_file, **{f"roi_{k}": v for k, v in merged.items()})
    return merged


def _match_rois(patterns, keys: list[str]) -> list[str]:
    """Resolve ROI name patterns (leading or trailing '*') to concrete Glasser
    parcel names present in the atlas."""
    out: list[str] = []
    for p in patterns:
        if p.endswith("*"):
            matched = [k for k in keys if k.startswith(p[:-1])]
        elif p.startswith("*"):
            matched = [k for k in keys if k.endswith(p[1:])]
        else:
            matched = [k for k in keys if k == p]
        if not matched:
            print(f"[scoring] warning: ROI pattern {p!r} matched no Glasser parcel")
        out.extend(matched)
    return sorted(set(out))


class EngagementScorer:
    """Holds the atlas->vertex mapping (built once) and scores prediction
    tensors. Construct one per process and reuse it."""

    def __init__(self, cache_dir: str | Path, tr_seconds: float):
        self.cache_dir = Path(cache_dir)
        self.tr = float(tr_seconds)
        self._families: list[dict] | None = None
        self._labels: dict | None = None

    def warmup(self) -> None:
        """Build the atlas eagerly (call once at startup, off the request path)."""
        self._family_rois()

    def _hcp_labels(self) -> dict:
        if self._labels is None:
            with _HCP_LOCK:
                if self._labels is None:
                    self._labels = _build_hcp_labels(self.cache_dir)
        return self._labels

    def _family_rois(self) -> list[dict]:
        """Per family: {parcel_name -> vertex indices}. Kept per-parcel so we can
        average within a parcel before averaging across parcels (parcel-balanced,
        so a large parcel doesn't dominate a family)."""
        if self._families is None:
            labels = self._hcp_labels()
            keys = list(labels.keys())
            families = []
            for fam in ENGAGEMENT_FAMILIES:
                names = _match_rois(fam["rois"], keys)
                families.append({n: np.asarray(labels[n], dtype=np.int64) for n in names})
            self._families = families
        return self._families

    def score(self, predictions: np.ndarray) -> dict:
        """Summarise predicted surface responses over the four engagement
        families into a 0-100 report. `predictions` is `(T, 20484)`."""
        pred = np.asarray(predictions, dtype=np.float64)
        if pred.ndim != 2:
            raise ValueError(
                f"TRIBE predictions must be a 2D (time, vertices) array; got {pred.shape}"
            )
        if pred.shape[1] != 2 * FS5_VERTICES_PER_HEMI:
            raise ValueError(
                "TRIBE predictions must use the fsaverage5 surface "
                f"({2 * FS5_VERTICES_PER_HEMI} vertices); got {pred.shape[1]}"
            )
        if pred.shape[0] == 0:
            raise ValueError("TRIBE returned zero prediction frames")
        if not np.isfinite(pred).all():
            raise ValueError("TRIBE predictions contain NaN or infinite values")
        n_frames = int(pred.shape[0])

        # Standardize across the cortex independently at each timestep. The
        # temporal average is therefore free to vary by clip; unlike temporal
        # per-vertex centering, this does not make the headline score identically 50.
        mu = pred.mean(axis=1, keepdims=True)
        sd = pred.std(axis=1, keepdims=True)
        sd[sd < 1e-6] = 1e-6
        z = (pred - mu) / sd

        families = self._family_rois()
        traces = np.zeros((len(ENGAGEMENT_FAMILIES), n_frames), dtype=np.float32)
        for i, parcels in enumerate(families):
            if not parcels:
                continue
            # Mean within each parcel, then mean across parcels (parcel-balanced).
            parcel_means = [z[:, idx].mean(axis=1) for idx in parcels.values()]
            fam_z = np.mean(np.stack(parcel_means, axis=0), axis=0)
            traces[i] = np.clip(
                50.0 * (1.0 + fam_z / CORTICAL_Z_REF),
                0,
                100,
            )

        regions = []
        cognitive_series = {}
        for i, fam in enumerate(ENGAGEMENT_FAMILIES):
            values = np.round(traces[i], 1).tolist()
            cognitive_series[fam["key"]] = values
            regions.append({
                "name": fam["name"],
                "short": fam["short"],
                "color": fam["color"],
                "reliability": fam["reliability"],
                "score": round(float(traces[i].mean()), 1) if n_frames else 0.0,
                "values": values,
            })

        # Overall engagement = equal-weighted mean of the four families.
        global_trace = traces.mean(axis=0)
        activation_score = round(float(np.mean([r["score"] for r in regions])), 1)
        regions.sort(key=lambda r: r["score"], reverse=True)

        peak_index = int(np.argmax(global_trace)) if n_frames else 0
        trough_index = int(np.argmin(global_trace)) if n_frames else 0
        peak_label = regions[0]["short"] if regions else "CORTEX"
        return {
            "duration": round(n_frames * self.tr, 2),
            "frames": n_frames,
            "tr": round(self.tr, 4),
            "source": "model",
            "activationScore": activation_score,
            "global": np.round(global_trace, 2).tolist(),
            "regions": regions,
            "cognitiveSeries": cognitive_series,
            "peak": {
                "time": round(peak_index * self.tr, 2),
                "label": peak_label,
                "value": round(float(global_trace[peak_index]), 2) if n_frames else 0.0,
            },
            "trough": {
                "time": round(trough_index * self.tr, 2),
                "value": round(float(global_trace[trough_index]), 2) if n_frames else 0.0,
            },
            "weakWindow": _weak_window(global_trace, self.tr),
        }


def _weak_window(global_trace: np.ndarray, tr: float, win: int = 3) -> dict:
    """Lowest-engagement contiguous window (the beat an optimizer should
    regenerate first). Returns the [start, end] time span and its mean score."""
    n = len(global_trace)
    if n == 0:
        return {"startTime": 0.0, "endTime": 0.0, "meanValue": 0.0}
    w = min(win, n)
    sums = np.convolve(global_trace, np.ones(w, dtype=np.float64), mode="valid")
    start = int(np.argmin(sums))
    return {
        "startTime": round(start * tr, 2),
        "endTime": round((start + w) * tr, 2),
        "meanValue": round(float(sums[start] / w), 2),
    }
