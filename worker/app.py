"""FastAPI inference worker for facebook/tribev2.

Returns compact ROI traces rather than streaming the entire ~20k-vertex
surface tensor to a browser. The model still runs its full cortical prediction.
"""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

import numpy as np
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from tribev2 import TribeModel
from utils import splice_video, video_duration

# Use Ampere+ TF32 tensor cores for fp32 matmuls (the V-JEPA2 ViT is matmul-heavy):
# a large speedup on GPUs like the A100 with negligible accuracy change. No-op on CPU.
torch.set_float32_matmul_precision("high")
torch.backends.cudnn.allow_tf32 = True

MODEL_ID = os.getenv("TRIBEV2_MODEL_ID", "facebook/tribev2")
# Docker supplies /data/cache. For a native local run, keep the downloaded
# features and weights beside the worker instead of assuming a writable /data.
CACHE_DIR = os.getenv("TRIBEV2_CACHE_DIR", str(Path(__file__).resolve().parent / ".cache"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(1_000_000_000)))
# Per-video prediction cache: each result is saved as <sha256-of-video>.json here,
# so re-uploading the same clip returns instantly instead of re-running inference.
PREDICTIONS_DIR = Path(os.getenv("TRIBEV2_PREDICTIONS_DIR", str(Path(__file__).resolve().parent.parent / "prediction_cache")))
# Downscale uploads so the shorter side is this many px before the encode loop. The
# model's input is 256x256, so 256 matches it exactly (a single resample, closest to
# the no-downscale result) and makes frame DECODING (the real bottleneck) far cheaper
# with ~unchanged scores. Don't go below 256 (that forces upscaling, losing detail).
# Set TRIBEV2_DOWNSCALE=0 to disable.
DOWNSCALE_TARGET = int(os.getenv("TRIBEV2_DOWNSCALE", "256"))
model: TribeModel | None = None


def _configure_mac_ffmpeg() -> None:
    """WhisperX's VAD uses torchcodec, which needs FFmpeg 4-7 (libavutil 56-59).
    Homebrew's default ffmpeg is now v8 (libavutil 60), which torchcodec cannot
    load. On macOS, if a compatible ffmpeg@N is installed, expose its libs to the
    WhisperX subprocess via DYLD_FALLBACK_LIBRARY_PATH. No-op off macOS and on
    GPU/Linux boxes, where the system FFmpeg already works (device-adaptive)."""
    import glob
    import sys

    if sys.platform != "darwin":
        return
    found = []
    for opt in ("/opt/homebrew/opt", "/usr/local/opt"):
        for ver in ("ffmpeg@7", "ffmpeg@6", "ffmpeg@5", "ffmpeg@4"):
            libdir = Path(opt) / ver / "lib"
            if libdir.is_dir() and glob.glob(str(libdir / "libavutil.5*.dylib")):
                found.append(str(libdir))
    if not found:
        return
    existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(found + ([existing] if existing else []))


_configure_mac_ffmpeg()

# Engagement families defined on TRIBE v2's native output space (the fsaverage5
# cortical surface) using the model authors' built-in HCP-MMP / Glasser atlas
# (tribev2.utils.get_hcp_labels), instead of hand-placed spheres. Each family is
# the union of named Glasser parcels (wildcards: "STG*" matches STGa, STGda, ...),
# chosen from the engagement-neuroscience literature.
#
# `reliability` records how well TRIBE itself predicts that territory (paper
# Sec. 3.2-3.3: auditory/language are near the noise ceiling; association areas
# are well predicted by the multimodal model; primary visual is weaker, so it is
# intentionally excluded from the visual family). It is surfaced to the user as
# an honest confidence note ONLY and does NOT weight the score -- all four
# families contribute equally.
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

# Within-video scale, centered on each region's own baseline: a family-mean of
# 0 SD maps to 50, +ENGAGEMENT_Z_REF SD to 100, -ENGAGEMENT_Z_REF SD to 0. Fixed
# and shared across the four families so they are directly comparable within a
# clip. (Cross-video comparability needs a fixed reference set -- deferred.)
ENGAGEMENT_Z_REF = 2.0


# fsaverage mesh resolution for the WebGL viewer. fsaverage5 (10,242 vertices
# per hemisphere) is TRIBE v2's native prediction resolution, so the mesh and
# the predicted activation map onto the same vertices with no interpolation,
# while still resolving real gyral/sulcal folds. fsaverage6 is available for an
# even smoother anatomical surface (predictions are then nearest-neighbour
# upsampled), at the cost of a larger payload.
SURFACE_MESH = os.getenv("TRIBEV2_SURFACE_MESH", "fsaverage5")
_MESH_VERTICES = {"fsaverage4": 2562, "fsaverage5": 10242, "fsaverage6": 40962}


_HCP_LOCK = threading.Lock()


def _build_hcp_labels() -> dict:
    """Replicate tribev2.utils.get_hcp_labels WITHOUT its ~1.65 GB MNE *sample*
    dataset download. We only need a subjects_dir that contains `fsaverage`, so we
    use the small `fetch_fsaverage` (~tens of MB) + the HCP-MMP annotation, then
    apply the same fsaverage->fsaverage5 downsample (keep vertices < 10242) and
    left/right stacking. Result is cached to an .npz so later starts need no MNE.
    """
    cache_file = Path(CACHE_DIR) / "hcp_fsaverage5_labels.npz"
    if cache_file.exists():
        data = np.load(cache_file, allow_pickle=False)
        return {k[4:]: data[k] for k in data.files}  # strip the "roi_" key prefix

    import mne

    fs5 = _MESH_VERTICES["fsaverage5"]  # 10242 vertices per hemisphere
    subjects_dir = Path(CACHE_DIR) / "mne_subjects"
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

    Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)
    np.savez(cache_file, **{f"roi_{k}": v for k, v in merged.items()})
    return merged


@lru_cache(maxsize=1)
def _hcp_labels() -> dict:
    """{bare Glasser ROI name -> vertex indices into the stacked 20484-vertex
    fsaverage5 array (left 0..10241, right 10242..20483)}. Built once; the lock +
    on-disk .npz cache make concurrent first-calls single-flight, so a FastAPI
    threadpool can't trigger duplicate MNE downloads (the original bug)."""
    with _HCP_LOCK:
        return _build_hcp_labels()


def _match_rois(patterns, keys: list[str]) -> list[str]:
    """Resolve ROI name patterns (supporting a leading or trailing '*') to the
    concrete Glasser parcel names present in the atlas."""
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


@lru_cache(maxsize=1)
def _family_rois() -> list[dict]:
    """Per family: {parcel_name -> vertex indices}. Kept per-parcel so we can
    average within a parcel before averaging across parcels (parcel-balanced, so
    a large parcel doesn't dominate a family)."""
    labels = _hcp_labels()
    keys = list(labels.keys())
    families = []
    for fam in ENGAGEMENT_FAMILIES:
        names = _match_rois(fam["rois"], keys)
        families.append({n: np.asarray(labels[n], dtype=np.int64) for n in names})
    return families


@lru_cache(maxsize=1)
def _family_data_fs5() -> tuple[np.ndarray, np.ndarray]:
    """Per-vertex family label (0..3, or -1 for none) + weight (1 inside a family
    ROI, else 0) over the stacked fsaverage5 surface, for the WebGL viewer. Built
    from the same Glasser parcels used for scoring; on a rare overlapping vertex
    the earlier family wins."""
    n_vertices = 2 * _MESH_VERTICES["fsaverage5"]
    families = np.full(n_vertices, -1, dtype=np.int8)
    weights = np.zeros(n_vertices, dtype=np.float32)
    for fi, parcels in enumerate(_family_rois()):
        for idx in parcels.values():
            unassigned = idx[families[idx] < 0]
            families[unassigned] = fi
            weights[unassigned] = 1.0
    return families, weights


@lru_cache(maxsize=1)
def get_surface_mesh() -> dict:
    """Return a real fsaverage pial mesh for anatomical WebGL display.

    The pial surface preserves the physical cortical folds and carries its
    native left/right positions, so the two hemispheres assemble into an
    anatomically correct whole brain. Per-vertex curvature is included so the
    client can render the classic gyral (light) / sulcal (dark) shading that
    makes the surface read as a real cortex, with the predicted response
    overlaid on top.
    """
    from nilearn.datasets import fetch_surf_fsaverage
    from nilearn.surface import load_surf_data, load_surf_mesh

    fsaverage = fetch_surf_fsaverage(mesh=SURFACE_MESH, data_dir=str(Path(CACHE_DIR) / "nilearn"))
    vph = _MESH_VERTICES.get(SURFACE_MESH, 10242)
    output = {"mesh": SURFACE_MESH, "verticesPerHemisphere": vph, "surface": "pial", "hemispheres": {}}
    families, weights = _family_data_fs5()
    fam_per_hemi = {"left": families[:10242], "right": families[10242:]}
    wt_per_hemi = {"left": weights[:10242], "right": weights[10242:]}
    for hemi in ("left", "right"):
        coords, faces = load_surf_mesh(getattr(fsaverage, f"pial_{hemi}"))
        # FreeSurfer curvature is positive in sulci and negative on gyri. Squash
        # it into a stable 0..1 "depth" cue (1 = deep sulcus → darker) so the
        # base brain shows folds independent of any activation overlay.
        curv = load_surf_data(getattr(fsaverage, f"curv_{hemi}")).astype(np.float32)
        depth = (np.tanh(curv / (np.std(curv) * 1.5 + 1e-6)) + 1.0) / 2.0
        # Map the fsaverage5 family labels onto the served resolution (1:1 at
        # fsaverage5; nested slice / nearest upsample otherwise).
        fam = resample_to_mesh(fam_per_hemi[hemi][None, :], hemi, 10242)[0]
        wt = resample_to_mesh(wt_per_hemi[hemi][None, :], hemi, 10242)[0]
        output["hemispheres"][hemi] = {
            "positions": np.round(coords.astype(np.float32), 3).ravel().tolist(),
            "indices": faces.astype(np.uint32).ravel().tolist(),
            "curvature": np.round(depth, 3).tolist(),
            "families": fam.astype(int).tolist(),
            "weights": np.round(wt, 3).tolist(),
        }
    return output


@asynccontextmanager
async def lifespan(_: FastAPI):
    global model
    # Opt-in V-JEPA2 encode speedups (batched forwards + decode/forward overlap).
    # No-op unless TRIBEV2_BATCH>1 or TRIBEV2_PREFETCH=1, so the default path is
    # the untouched stock neuralset loop. Must run before the first /predict.
    try:
        from vjepa_fastpath import install_fastpath

        install_fastpath()
    except Exception as exc:  # never block startup on an optional speedup
        print(f"[vjepa-fastpath] not installed: {exc}")
    # This is deliberately loaded once: weights and feature extractors are large.
    model = TribeModel.from_pretrained(MODEL_ID, cache_folder=CACHE_DIR)
    # Build the atlas->vertex mapping once, at startup, so no request ever triggers
    # it from the threadpool (which previously spawned duplicate MNE downloads).
    try:
        _family_rois()
    except Exception as exc:  # don't block startup; first request will retry
        print(f"[scoring] atlas warmup failed (will retry on request): {exc}")
    yield
    model = None


app = FastAPI(title="Cerebra TRIBE v2 worker", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


@lru_cache(maxsize=2)
def _upsample_index(hemi: str, source_vertices: int) -> np.ndarray:
    """Nearest source vertex for each mesh vertex (only used when the mesh is
    finer than the fsaverage5 prediction, e.g. fsaverage6)."""
    from nilearn.datasets import fetch_surf_fsaverage
    from nilearn.surface import load_surf_mesh
    from scipy.spatial import cKDTree

    fine = fetch_surf_fsaverage(mesh=SURFACE_MESH, data_dir=str(Path(CACHE_DIR) / "nilearn"))
    coarse = fetch_surf_fsaverage(mesh="fsaverage5", data_dir=str(Path(CACHE_DIR) / "nilearn"))
    fine_coords, _ = load_surf_mesh(getattr(fine, f"pial_{hemi}"))
    coarse_coords, _ = load_surf_mesh(getattr(coarse, f"pial_{hemi}"))
    _, idx = cKDTree(coarse_coords[:source_vertices]).query(fine_coords, k=1)
    return idx.astype(np.int64)


def resample_to_mesh(values: np.ndarray, hemi: str, source_vertices: int) -> np.ndarray:
    """Map a per-vertex fsaverage5 trace onto the served mesh resolution."""
    vph = _MESH_VERTICES.get(SURFACE_MESH, source_vertices)
    if vph == source_vertices:
        return values
    if vph < source_vertices:
        # fsaverage meshes are hierarchically nested: the first `vph` vertices
        # are the coarser level, so a slice is an exact downsample.
        return values[:, :vph]
    return values[:, _upsample_index(hemi, source_vertices)]


def reference_stats(predictions: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-vertex temporal mean/SD over a clip — the within-video baseline.
    Persisted per scored video so later clips (regenerated takes) can be
    z-scored against THIS video's baseline instead of their own, making their
    engagement scores comparable across clips (the fixed reference the model
    authors deferred). SD is floored to avoid divide-by-zero on flat vertices."""
    pred = np.asarray(predictions, dtype=np.float64)
    mu = pred.mean(axis=0)
    sd = pred.std(axis=0)
    sd[sd < 1e-6] = 1e-6
    return mu, sd


def build_response(predictions: np.ndarray, ref: tuple[np.ndarray, np.ndarray] | None = None) -> dict:
    """Summarise predicted surface responses over the four engagement families.

    Default (ref=None): within-video, signed, per-vertex baseline — each vertex
    is z-scored against its OWN temporal mean/SD over this clip, so a family
    trace reflects how far that territory deviates from its own baseline
    (50 = baseline, 100 = +ENGAGEMENT_Z_REF SD, 0 = -ENGAGEMENT_Z_REF SD).

    When ref=(mu, sd) is supplied (per-vertex arrays from a previous clip), each
    vertex is z-scored against THAT reference instead of its own clip, making
    the resulting engagement scores directly comparable to the original video.

    Traces are parcel-balanced and placed on one fixed 0..100 scale so the four
    families are directly comparable within a result. All four are weighted
    equally; `reliability` is reported but never weights the score. Overall
    engagement is the equal-weighted mean of the four families.
    """
    assert model is not None
    pred = np.asarray(predictions, dtype=np.float64)
    n_frames = int(pred.shape[0])

    # Per-vertex baseline (signed z): (value - mean) / SD. When `ref` is given we
    # z-score against ANOTHER video's baseline (the original), so this clip's
    # engagement is measured relative to the original rather than to itself.
    if ref is None:
        mu = pred.mean(axis=0, keepdims=True)
        sd = pred.std(axis=0, keepdims=True)
    else:
        mu = np.asarray(ref[0], dtype=np.float64).reshape(1, -1)
        sd = np.asarray(ref[1], dtype=np.float64).reshape(1, -1)
    sd = np.where(sd < 1e-6, 1e-6, sd)
    z = (pred - mu) / sd  # (T, V) signed; > 0 means above the (reference) baseline

    families = _family_rois()
    traces = np.zeros((len(ENGAGEMENT_FAMILIES), n_frames), dtype=np.float32)
    for i, parcels in enumerate(families):
        if not parcels:
            continue
        # Mean within each parcel, then mean across parcels (parcel-balanced).
        parcel_means = [z[:, idx].mean(axis=1) for idx in parcels.values()]
        fam_z = np.mean(np.stack(parcel_means, axis=0), axis=0)
        # Centered scale: baseline (0 SD) -> 50, +/-ENGAGEMENT_Z_REF SD -> 100/0.
        traces[i] = np.clip(50.0 * (1.0 + fam_z / ENGAGEMENT_Z_REF), 0, 100)

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

    # Overall engagement = equal-weighted mean of the four families (no
    # reliability weighting, per design).
    global_trace = traces.mean(axis=0)
    engagement_score = round(float(np.mean([r["score"] for r in regions])), 1)
    regions.sort(key=lambda r: r["score"], reverse=True)

    tr = float(model.data.TR)
    peak_index = int(np.argmax(global_trace)) if n_frames else 0
    peak_label = regions[0]["short"] if regions else "CORTEX"
    return {
        "duration": round(n_frames * tr, 2),
        "frames": n_frames,
        "source": "model",
        "engagementScore": engagement_score,
        "global": np.round(global_trace, 2).tolist(),
        "regions": regions,
        "cognitiveSeries": cognitive_series,
        "peak": {"time": round(peak_index * tr, 2), "label": peak_label, "value": round(float(global_trace[peak_index]), 2) if n_frames else 0.0},
    }


@app.get("/health")
def health():
    return {"ready": model is not None, "model": MODEL_ID}


@app.get("/surface")
def surface_mesh():
    return get_surface_mesh()


def _maybe_downscale(src: Path) -> Path:
    """ffmpeg-downscale the video's shorter side to DOWNSCALE_TARGET px before the
    encode loop, so frame decoding (the real bottleneck) is cheap. V-JEPA2 resizes
    to 256x256 regardless, so scores are ~unchanged. Falls back to the original on
    ANY failure (ffmpeg missing, odd codec, timeout) so a predict never breaks here."""
    if DOWNSCALE_TARGET <= 0:
        return src
    out = src.with_name(f"{src.stem}_ds.mp4")
    t = DOWNSCALE_TARGET
    # Scale whichever side is shorter to <= t, preserving aspect, never upscaling.
    vf = f"scale='if(gt(iw,ih),-2,min({t},iw))':'if(gt(iw,ih),min({t},ih),-2)'"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
           "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(out)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        if out.exists() and out.stat().st_size > 0:
            print(f"[downscale] {src.name}: shorter side -> <= {t}px")
            return out
    except Exception as exc:
        print(f"[downscale] skipped, using original ({exc})")
    return src


@app.post("/splice")
async def splice(
    video: UploadFile = File(...),
    ranges: str = Form(...),
    as_fraction: bool = Form(True),
):
    """Remove the given time ranges from an uploaded video and return the
    spliced mp4. `ranges` is a JSON array of [start, end] pairs — interpreted as
    fractions of the clip duration when `as_fraction` is true (the default), so
    the browser can mark cuts without knowing the exact container duration."""
    if not video.content_type or not video.content_type.startswith("video/"):
        raise HTTPException(status_code=415, detail="Upload a video file.")
    suffix = Path(video.filename or "clip.mp4").suffix.lower()
    if suffix not in {".mp4", ".mov", ".webm", ".avi", ".mkv"}:
        raise HTTPException(status_code=415, detail="Use MP4, MOV, WebM, AVI, or MKV.")
    try:
        pairs = [(float(a), float(b)) for a, b in json.loads(ranges)]
    except (ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="`ranges` must be JSON like [[start,end],...].")
    if not pairs:
        raise HTTPException(status_code=400, detail="No cut ranges supplied.")

    work = Path(tempfile.mkdtemp(prefix="splice-job-"))
    source = work / f"input{suffix}"
    written = 0
    with source.open("wb") as output:
        while chunk := await video.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Video exceeds upload limit.")
            output.write(chunk)
    await video.close()

    try:
        if as_fraction:
            duration = video_duration(source)
            pairs = [(a * duration, b * duration) for a, b in pairs]
        out_path = splice_video(source, pairs, out_path=work / "spliced.mp4")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Splice failed: {exc}") from exc

    from starlette.background import BackgroundTask
    import shutil

    name = f"{Path(video.filename or 'clip').stem}_spliced.mp4"
    return FileResponse(
        out_path,
        media_type="video/mp4",
        filename=name,
        background=BackgroundTask(lambda: shutil.rmtree(work, ignore_errors=True)),
    )


@app.post("/predict")
async def predict(video: UploadFile = File(...)):
    if not video.content_type or not video.content_type.startswith("video/"):
        raise HTTPException(status_code=415, detail="Upload a video file.")
    suffix = Path(video.filename or "clip.mp4").suffix.lower()
    if suffix not in {".mp4", ".mov", ".webm", ".avi", ".mkv"}:
        raise HTTPException(status_code=415, detail="Use MP4, MOV, WebM, AVI, or MKV.")
    if model is None:
        raise HTTPException(status_code=503, detail="Model is still loading.")

    PREDICTIONS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tribev2-") as tmp:
        destination = Path(tmp) / f"input{suffix}"
        hasher = hashlib.sha256()
        written = 0
        with destination.open("wb") as output:
            while chunk := await video.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Video exceeds upload limit.")
                hasher.update(chunk)
                output.write(chunk)
        await video.close()

        # Content-addressed cache: identical video bytes -> identical key -> reuse.
        digest = hasher.hexdigest()
        cache_file = PREDICTIONS_DIR / f"{digest}.json"
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text())
                cached["cached"] = True
                # Scoring (/score_takes) reuses the original's per-vertex baseline
                # (.ref.npz). A fresh /predict saves it below, but cache entries
                # from before the scoring feature — or after a cleanup — may lack
                # it, which would make /score_takes 409. Recompute it on the GPU
                # so a cached original can ALWAYS be scored against; once saved,
                # future scoring is instant.
                ref_file = PREDICTIONS_DIR / f"{digest}.ref.npz"
                if not ref_file.exists() and model is not None:
                    try:
                        infer_path = _maybe_downscale(destination)
                        events = model.get_events_dataframe(video_path=str(infer_path))
                        predictions, _ = model.predict(events, verbose=False)
                        mu, sd = reference_stats(predictions)
                        np.savez(ref_file, mu=mu, sd=sd)
                        print(f"[cache] recomputed missing baseline for {digest[:12]}")
                    except Exception as exc:
                        print(f"[cache] could not recompute baseline {digest[:12]}: {exc}")
                print(f"[cache] hit {digest[:12]} -> returning saved result")
                return cached
            except Exception as exc:  # corrupt entry: fall through and recompute
                print(f"[cache] ignoring unreadable {cache_file.name}: {exc}")

        try:
            infer_path = _maybe_downscale(destination)
            # No bf16 autocast at this level. The heavy V-JEPA2 "Encoding video" loop
            # runs INSIDE model.predict (its DataLoader drives the feature extractors),
            # NOT inside get_events_dataframe (which is ASR + event assembly). And
            # wrapping model.predict wholesale in bf16 makes the TRIBE head's output
            # bf16, which tribev2 then .cpu().numpy()s -> "Got unsupported ScalarType
            # BFloat16". So bf16 is applied surgically to just the V-JEPA2 forward in
            # vjepa_fastpath (the actual bottleneck), leaving the head fp32 here.
            t0 = time.perf_counter()
            events = model.get_events_dataframe(video_path=str(infer_path))
            t1 = time.perf_counter()
            predictions, _ = model.predict(events, verbose=False)
            t2 = time.perf_counter()
            # get_events = ASR/event assembly; predict = TRIBE head AND the V-JEPA2
            # video encode (watch the "Encoding video N/N" tqdm bar for the latter).
            print(f"[timing] get_events={t1 - t0:.1f}s predict={t2 - t1:.1f}s")
            result = build_response(predictions)
        except Exception as exc:
            # FastAPI won't log a traceback for the HTTPException below, so print
            # the real one to the worker log for diagnosis.
            import traceback

            print("[predict] inference FAILED:\n" + traceback.format_exc(), flush=True)
            raise HTTPException(status_code=500, detail=f"TRIBE v2 inference failed: {exc}") from exc

        result["referenceId"] = digest
        result["cached"] = False
        try:
            mu, sd = reference_stats(predictions)
            np.savez(PREDICTIONS_DIR / f"{digest}.ref.npz", mu=mu, sd=sd)
        except Exception as exc:
            print(f"[cache] failed to save reference {digest[:12]}: {exc}")
        try:
            cache_file.write_text(json.dumps(result))
            print(f"[cache] saved {digest[:12]} -> {cache_file}")
        except Exception as exc:
            print(f"[cache] failed to save {cache_file.name}: {exc}")
        return result


def _load_reference(reference_id: str) -> tuple[np.ndarray, np.ndarray] | None:
    """Load the persisted per-vertex baseline for an already-scored video."""
    safe = "".join(c for c in reference_id if c in "0123456789abcdefABCDEF")
    ref_file = PREDICTIONS_DIR / f"{safe}.ref.npz"
    if not ref_file.exists():
        return None
    data = np.load(ref_file)
    return data["mu"], data["sd"]


def _score_against_reference(video_path: Path, ref: tuple[np.ndarray, np.ndarray]) -> dict:
    """Run TRIBE on one clip and score it against the given reference baseline."""
    infer_path = _maybe_downscale(video_path)
    events = model.get_events_dataframe(video_path=str(infer_path))
    predictions, _ = model.predict(events, verbose=False)
    return build_response(predictions, ref=ref)


@app.post("/score_takes")
async def score_takes(referenceId: str = Form(...), takes: list[UploadFile] = File(...)):
    """Score regenerated takes against an already-scored ORIGINAL video.

    The original is NOT re-encoded: we reuse the per-vertex baseline saved by
    /predict (keyed by `referenceId` = the original's sha256). Each take is
    z-scored against that baseline, so the takes are directly comparable to one
    another and to the original. Returns one headline (mean of the 4 families)
    and the per-frame series per take, plus the best take index."""
    if model is None:
        raise HTTPException(status_code=503, detail="Model is still loading.")
    ref = _load_reference(referenceId)
    if ref is None:
        raise HTTPException(status_code=409, detail="Unknown referenceId — score the original first.")
    if not takes:
        raise HTTPException(status_code=400, detail="No takes supplied.")

    out: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="score-takes-") as tmp:
        for upload_index, up in enumerate(takes):
            match = re.search(r"take_(\d+)", up.filename or "")
            take_index = int(match.group(1)) - 1 if match else upload_index
            suffix = Path(up.filename or "take.mp4").suffix.lower() or ".mp4"
            dest = Path(tmp) / f"take_{upload_index}{suffix}"
            with dest.open("wb") as fh:
                while chunk := await up.read(1024 * 1024):
                    fh.write(chunk)
            await up.close()
            try:
                resp = _score_against_reference(dest, ref)
            except Exception as exc:
                import traceback
                print("[score_takes] inference FAILED:\n" + traceback.format_exc(), flush=True)
                raise HTTPException(status_code=500, detail=f"Take scoring failed: {exc}") from exc
            out.append({
                "takeIndex": take_index,
                "score": resp["engagementScore"],
                "factors": {r["short"]: r["score"] for r in resp["regions"]},
                "series": {"global": resp["global"], **{r["short"]: r["values"] for r in resp["regions"]}},
            })

    if not out:
        raise HTTPException(status_code=400, detail="No takes could be scored.")
    best = max(range(len(out)), key=lambda i: out[i]["score"])
    average = round(sum(t["score"] for t in out) / len(out), 1)
    return {"best": out[best]["takeIndex"], "average": average, "takes": out}
