"""FastAPI inference worker for facebook/tribev2.

Returns compact ROI traces rather than streaming the entire ~20k-vertex
surface tensor to a browser. The model still runs its full cortical prediction.
"""

import os
import tempfile
import threading
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from tribev2 import TribeModel

MODEL_ID = os.getenv("TRIBEV2_MODEL_ID", "facebook/tribev2")
# Docker supplies /data/cache. For a native local run, keep the downloaded
# features and weights beside the worker instead of assuming a writable /data.
CACHE_DIR = os.getenv("TRIBEV2_CACHE_DIR", str(Path(__file__).resolve().parent / ".cache"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(1_000_000_000)))
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

# Within-video scale: a family-mean response this many SDs above the region's own
# baseline maps to a score of 100. Fixed and shared across the four families so
# they are directly comparable within a clip. (Cross-video comparability needs a
# fixed reference set computed over many clips -- deferred to a later step.)
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


def build_response(predictions: np.ndarray) -> dict:
    """Summarise predicted surface responses over the four engagement families.

    Within-video, signed, per-vertex baseline: each vertex is z-scored against
    its OWN response over the clip, so a family trace reflects how far that
    territory rises above its baseline (positive = engaged; deactivation is not
    counted as engagement). Traces are parcel-balanced and placed on one fixed
    0..100 scale (ENGAGEMENT_Z_REF) so the four families are directly comparable.
    All four are weighted equally; `reliability` is reported but never weights the
    score. Overall engagement is the equal-weighted mean of the four families.
    """
    assert model is not None
    pred = np.asarray(predictions, dtype=np.float64)
    n_frames = int(pred.shape[0])

    # Per-vertex temporal baseline (signed): (value - vertex mean) / vertex SD.
    mu = pred.mean(axis=0, keepdims=True)
    sd = pred.std(axis=0, keepdims=True)
    sd[sd < 1e-6] = 1e-6
    z = (pred - mu) / sd  # (T, V) signed; > 0 means above this vertex's baseline

    families = _family_rois()
    traces = np.zeros((len(ENGAGEMENT_FAMILIES), n_frames), dtype=np.float32)
    for i, parcels in enumerate(families):
        if not parcels:
            continue
        # Mean within each parcel, then mean across parcels (parcel-balanced).
        parcel_means = [z[:, idx].mean(axis=1) for idx in parcels.values()]
        fam_z = np.mean(np.stack(parcel_means, axis=0), axis=0)
        # Positive engagement only, on the fixed shared scale.
        traces[i] = np.clip(100.0 * np.maximum(fam_z, 0.0) / ENGAGEMENT_Z_REF, 0, 100)

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


@app.post("/predict")
async def predict(video: UploadFile = File(...)):
    if not video.content_type or not video.content_type.startswith("video/"):
        raise HTTPException(status_code=415, detail="Upload a video file.")
    suffix = Path(video.filename or "clip.mp4").suffix.lower()
    if suffix not in {".mp4", ".mov", ".webm", ".avi", ".mkv"}:
        raise HTTPException(status_code=415, detail="Use MP4, MOV, WebM, AVI, or MKV.")
    if model is None:
        raise HTTPException(status_code=503, detail="Model is still loading.")

    with tempfile.TemporaryDirectory(prefix="tribev2-") as tmp:
        destination = Path(tmp) / f"input{suffix}"
        written = 0
        with destination.open("wb") as output:
            while chunk := await video.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Video exceeds upload limit.")
                output.write(chunk)
        try:
            events = model.get_events_dataframe(video_path=str(destination))
            predictions, _ = model.predict(events, verbose=False)
            return build_response(predictions)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"TRIBE v2 inference failed: {exc}") from exc
        finally:
            await video.close()
