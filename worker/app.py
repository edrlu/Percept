"""FastAPI inference worker for facebook/tribev2.

Returns compact ROI traces rather than streaming the entire ~20k-vertex
surface tensor to a browser. The model still runs its full cortical prediction.
"""

import os
import tempfile
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

import json

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from tribev2 import TribeModel
from utils import splice_video, video_duration

MODEL_ID = os.getenv("TRIBEV2_MODEL_ID", "facebook/tribev2")
# Docker supplies /data/cache. For a native local run, keep the downloaded
# features and weights beside the worker instead of assuming a writable /data.
CACHE_DIR = os.getenv("TRIBEV2_CACHE_DIR", str(Path(__file__).resolve().parent / ".cache"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(1_000_000_000)))
model: TribeModel | None = None

# Cortical surface proxy regions. TRIBE v2 predicts average-subject responses
# on the fsaverage5 cortical mesh; these manually defined regions are display
# summaries only. They are not direct measurements of reward, emotion,
# self-relevance, memory encoding, or subcortical structures.
#
# Each entry: key, displaoy label, side-panel colour, and a centre
# (|x| laterality, y anterior, z superior, radius) in FreeSurfer surface mm,
# projected onto the visible cortical mesh for the WebGL viewer.
ENGAGEMENT_FAMILIES = [
    {"key": "reward_desire",       "name": "Ventromedial PFC proxy", "short": "vmPFC", "color": "#ffb13b", "centroid": (15.0, 56.0, -10.0, 28.0)},
    {"key": "emotional_response",  "name": "Anterior temporal proxy", "short": "aTEMP", "color": "#ff5a7a", "centroid": (46.0, 12.0, -20.0, 30.0)},
    {"key": "personal_relevance",  "name": "Lateral PFC proxy",      "short": "lPFC", "color": "#9b8cff", "centroid": (40.0, 30.0, 40.0, 28.0)},
    {"key": "memory_encoding",     "name": "Ventral temporal proxy", "short": "vTEMP", "color": "#3fd6c0", "centroid": (47.0, -42.0, -10.0, 28.0)},
]
_FAMILY_CENTROIDS = [f["centroid"] for f in ENGAGEMENT_FAMILIES]


# fsaverage mesh resolution for the WebGL viewer. fsaverage5 (10,242 vertices
# per hemisphere) is TRIBE v2's native prediction resolution, so the mesh and
# the predicted activation map onto the same vertices with no interpolation,
# while still resolving real gyral/sulcal folds. fsaverage6 is available for an
# even smoother anatomical surface (predictions are then nearest-neighbour
# upsampled), at the cost of a larger payload.
SURFACE_MESH = os.getenv("TRIBEV2_SURFACE_MESH", "fsaverage5")
_MESH_VERTICES = {"fsaverage4": 2562, "fsaverage5": 10242, "fsaverage6": 40962}


def _assign_families(coords: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Label each vertex with the nearest cognitive family and a 0..1 falloff
    weight (1 at the territory centre → 0 at its edge), so activation fades
    smoothly into a heat-map gradient rather than a hard-edged patch."""
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
def _family_data_fs5() -> tuple[np.ndarray, np.ndarray]:
    """Per-vertex family + falloff weight over fsaverage5 (left then right)."""
    from nilearn.datasets import fetch_surf_fsaverage
    from nilearn.surface import load_surf_mesh

    fsaverage = fetch_surf_fsaverage(mesh="fsaverage5", data_dir=str(Path(CACHE_DIR) / "nilearn"))
    left, _ = load_surf_mesh(fsaverage.pial_left)
    right, _ = load_surf_mesh(fsaverage.pial_right)
    fam_l, w_l = _assign_families(left)
    fam_r, w_r = _assign_families(right)
    return np.concatenate([fam_l, fam_r]), np.concatenate([w_l, w_r])


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
    """Summarise predicted surface responses over four display proxy regions."""
    assert model is not None
    # Standardize globally only for visualization; raw fMRI-like values remain
    # available on the worker if an export endpoint is added later.
    mean = predictions.mean()
    std = predictions.std() or 1.0
    z = np.abs((predictions - mean) / std)
    global_trace = np.clip(z.mean(axis=1) * 28 + 18, 0, 100)

    # Per-engagement-system response over time, grouped by the cortical
    # territories the viewer lights up. Normalised jointly so the dominant
    # system reads brightest on the brain.
    families, _ = _family_data_fs5()
    keys = [f["key"] for f in ENGAGEMENT_FAMILIES]
    traces = np.zeros((len(keys), z.shape[0]), dtype=np.float32)
    for i in range(len(keys)):
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

    tr = float(model.data.TR)
    peak_index = int(np.argmax(global_trace))
    peak_label = regions[0]["short"] if regions else "CORTEX"
    return {
        "duration": round(len(global_trace) * tr, 2),
        "frames": int(len(global_trace)),
        "source": "model",
        "global": np.round(global_trace, 2).tolist(),
        "regions": regions,
        "cognitiveSeries": cognitive_series,
        "peak": {"time": round(peak_index * tr, 2), "label": peak_label, "value": round(float(global_trace[peak_index]), 2)},
    }


@app.get("/health")
def health():
    return {"ready": model is not None, "model": MODEL_ID}


@app.get("/surface")
def surface_mesh():
    return get_surface_mesh()


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
