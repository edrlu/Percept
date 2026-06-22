"""Generate cerebra_tribev2_server.ipynb from the real server source files.

Run from the plugin root:  python colab/_build_notebook.py
Embedding the live engagement.py / server.py via %%writefile keeps the Colab
notebook self-contained (upload-and-run) AND identical to the source of truth.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGAGEMENT = (ROOT / "server" / "engagement.py").read_text()
VIDEO_FEATURES = (ROOT / "server" / "video_features.py").read_text()
AD_SCORE = (ROOT / "server" / "ad_score.py").read_text()
SERVER = (ROOT / "server" / "server.py").read_text()
YOLO_SHA256 = "f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36"
YOLO_URL = "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt"
TRIBEV2_COMMIT = "38b3db073a7fa5bfbfc7fdd894b1a3536e4553e3"


def md(text):
    # The md() call sites use literal "\n" for readability; turn them into real
    # newlines so the markdown renders (and splitlines actually splits).
    text = text.replace("\\n", "\n")
    return {"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)}


def code(text):
    return {"cell_type": "code", "metadata": {}, "execution_count": None,
            "outputs": [], "source": text.splitlines(keepends=True)}


INSTALL = '''\
# 1. Install TRIBE v2 + serving deps, then AUTO-RESTART the kernel.
#
#    Do NOT import numpy in this cell. Colab already has a different numpy loaded
#    in memory; importing the freshly-pinned 2.2.6 build in the SAME process makes
#    new C-extensions bind to the stale module and `import numpy` crashes inside
#    _override___module__ (the AttributeError: 'numpy.ufunc' ... '__module__').
#    The fix is: install -> restart the kernel -> import (next cell).
import importlib.metadata as metadata
import shutil, subprocess, sys

def pip(*pkgs, force=False):
    cmd = [sys.executable, "-m", "pip", "install", "-q", "--no-cache-dir"]
    if force: cmd.append("--force-reinstall")
    subprocess.run(cmd + list(pkgs), check=True)

# Idempotency guard: if a prior run already installed everything (e.g. you are
# re-running this cell AFTER the restart), skip install + restart so the notebook
# just continues. Safe to import here — on the very first run tribev2 is absent,
# so we fall through to install + restart.
try:
    import numpy, transformers, tribev2  # noqa
    _ready = (
        numpy.__version__ == "2.2.6"
        and transformers.__version__ == "5.12.1"
        and metadata.version("torch").startswith("2.6.0")
        and metadata.version("torchaudio").startswith("2.6.0")
        and shutil.which("uvx") is not None
    )
except Exception:
    _ready = False
if _ready:
    print("Already installed (pinned NumPy/Transformers + TRIBE v2 + uv). Skipping — continue to the next cell.")
    raise SystemExit  # ends this cell cleanly without touching the kernel

print("Installing NumPy 2.2.6 (TRIBE v2's pin) ..."); pip("numpy==2.2.6", force=True)
print("Installing pinned TRIBE v2 revision ...");     pip("git+https://github.com/facebookresearch/tribev2.git@''' + TRIBEV2_COMMIT + '''")
# TRIBE v2's audio path imports transformers.Wav2Vec2BertModel; Colab's default
# transformers doesn't expose it. Pin the version the model is known to work with
# (matches a verified local run). Installed AFTER tribev2 so it wins.
print("Pinning transformers==5.12.1 (Wav2Vec2BertModel) ...")
# TRIBE already installed Transformers' dependency set. Use --no-deps here:
# a force reinstall with dependencies upgrades NumPy behind TRIBE's exact
# numpy==2.2.6 pin.
subprocess.run(
    [
        sys.executable, "-m", "pip", "install", "-q", "--no-cache-dir",
        "--force-reinstall", "--no-deps", "transformers==5.12.1",
    ],
    check=True,
)
print("Installing uv + OpenCV + MNE + serving deps ..."); pip(
    "uv>=0.8", "opencv-python-headless>=4.10", "ultralytics>=8.3", "mne>=1.6",
    "fastapi>=0.110", "uvicorn[standard]>=0.29", "python-multipart>=0.0.9"
)
# Some broad downstream requirements allow a newer NumPy. Restore TRIBE's
# exact pin last, then restart before any compiled extension imports it.
print("Restoring TRIBE's NumPy 2.2.6 pin ..."); pip("numpy==2.2.6", force=True)
# TRIBE requires torch<2.7, so pip downgrades current Colab to torch 2.6/cu124.
# Colab's preinstalled torchaudio can remain at a newer ABI (for example
# 2.11/cu128), which crashes on import with `aoti_torch_abi_version`. Match it
# explicitly without allowing pip to replace the already-correct torch build.
print("Matching torchaudio to TRIBE's torch 2.6/cu124 ABI ...")
subprocess.run(
    [
        sys.executable, "-m", "pip", "install", "-q", "--no-cache-dir",
        "--force-reinstall", "--no-deps",
        "--index-url", "https://download.pytorch.org/whl/cu124",
        "torchaudio==2.6.0+cu124",
    ],
    check=True,
)

print("\\nInstall done — restarting the kernel so the new NumPy loads cleanly.")
print("When it reconnects, run the cells BELOW (do NOT re-run this one).")
import IPython
IPython.Application.instance().kernel.do_shutdown(restart=True)
'''

VERIFY = '''\
# 2. Verify — run this AFTER the kernel has restarted.
import shutil, subprocess
import cv2, numpy, torch, torchaudio, transformers, tribev2, ultralytics

assert torch.cuda.is_available(), (
    "CUDA is unavailable. In Colab choose Runtime → Change runtime type → T4 GPU, "
    "then reconnect and run this cell again."
)
assert numpy.__version__ == "2.2.6", f"Expected NumPy 2.2.6, got {numpy.__version__}"
assert torchaudio.__version__.startswith("2.6.0"), (
    f"Expected torchaudio 2.6.x for torch 2.6, got {torchaudio.__version__}"
)
assert shutil.which("ffmpeg"), "ffmpeg is missing"
assert shutil.which("uvx"), "uvx is missing; re-run the install cell once"
from transformers import Wav2Vec2BertModel  # noqa: F401

print("OK — NumPy", numpy.__version__,
      "| OpenCV", cv2.__version__,
      "| Ultralytics", ultralytics.__version__,
      "| Transformers", transformers.__version__,
      "| Torch", torch.__version__,
      "| Torchaudio", torchaudio.__version__,
      "| GPU", torch.cuda.get_device_name(0),
      "| tribev2 imported cleanly")
'''

LAUNCH = '''\
# 4. Launch the scorer + a public tunnel. Copy the printed https URL into the
#    Pika skill (NEURO_API_URL). Set a key so the public tunnel isn't open.
import hashlib, os, re, sys, time, subprocess, secrets, urllib.request, json
from pathlib import Path

# ---- config -------------------------------------------------------------
os.environ["TRIBEV2_CACHE_DIR"] = "/content/tribev2-cache"
# Universal default: no gated LLaMA access is required. Change this to "auto"
# only if you have accepted the LLaMA 3.2 license and configured HF_TOKEN.
os.environ["TRIBEV2_TEXT_MODE"] = "off"
os.environ["TRIBEV2_MODALITIES"] = "video"
os.environ["TRIBEV2_UPSTREAM_COMMIT"] = "''' + TRIBEV2_COMMIT + '''"
# hf-xet stalled during a real Colab T4 test after transferring ~3 GB of the
# 4.14 GB V-JEPA2 checkpoint, leaving a 768 MB incomplete file and a request
# hanging for 30 minutes. The verified encoder prefetch below uses curl instead.
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "600"
os.environ["HF_HUB_ETAG_TIMEOUT"] = "60"
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
os.environ["YOLO_MODEL"] = "/content/yolov8n.pt"
os.environ.setdefault("NEURO_API_KEY", secrets.token_urlsafe(16))  # shared secret
from google.colab import userdata
try:
    _hf_token = userdata.get("HF_TOKEN")
    if _hf_token:
        os.environ["HF_TOKEN"] = _hf_token
        os.environ["HUGGING_FACE_HUB_TOKEN"] = _hf_token
        print("HF_TOKEN found. The server will verify LLaMA 3.2 access.")
    else:
        print("HF_TOKEN is empty — public video-only mode still works.")
except Exception:
    print("No readable HF_TOKEN secret — public video-only mode still works.")

# ---- deterministic encoder prefetch --------------------------------------
# Do not let the first user's /score request become a hidden 4 GB model
# download. Fetch to a normal local directory, resume safely, enforce a
# minimum transfer speed, and verify both exact size and SHA-256.
ENCODER_ROOT = Path("/content/tribev2-encoders")
VIDEO_ENCODER = ENCODER_ROOT / "vjepa2-vitg-fpc64-256"

def fetch_public_model(repo, destination, expected_size, expected_sha):
    destination.mkdir(parents=True, exist_ok=True)
    files = ("config.json", "video_preprocessor_config.json", "model.safetensors")

    def file_sha256(path):
        digest = hashlib.sha256()
        with open(path, "rb") as stream:
            while chunk := stream.read(16 * 1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    for filename in files:
        target = destination / filename
        url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
        if filename != "model.safetensors":
            subprocess.run(
                ["curl", "-L", "--fail", "--retry", "5", "--retry-all-errors",
                 "--connect-timeout", "30", "-o", str(target), url],
                check=True,
            )
            continue

        if target.exists():
            size_ok = target.stat().st_size == expected_size
            sha_ok = size_ok and file_sha256(target) == expected_sha
            if size_ok and sha_ok:
                print(f"{repo} already verified ({expected_size / 1e9:.2f} GB).")
                continue
            target.unlink()
        partial = target.with_suffix(target.suffix + ".part")
        if partial.exists() and partial.stat().st_size > expected_size:
            partial.unlink()
        command = [
            "curl", "-L", "--fail", "--retry", "12", "--retry-all-errors",
            "--connect-timeout", "30", "--speed-limit", "1024",
            "--speed-time", "120", "-o", str(partial),
        ]
        if partial.exists():
            command += ["-C", "-"]
        command.append(url)
        print(f"Downloading {repo} ({expected_size / 1e9:.2f} GB); resumable...")
        subprocess.run(command, check=True)
        actual_size = partial.stat().st_size
        if actual_size != expected_size:
            raise RuntimeError(
                f"{repo} size mismatch: expected {expected_size}, got {actual_size}"
            )
        actual_sha = file_sha256(partial)
        if actual_sha != expected_sha:
            raise RuntimeError(
                f"{repo} SHA-256 mismatch: expected {expected_sha}, got {actual_sha}"
            )
        partial.replace(target)
        print(f"Verified {repo}: {actual_size / 1e9:.2f} GB, SHA-256 OK.")

fetch_public_model(
    "facebook/vjepa2-vitg-fpc64-256",
    VIDEO_ENCODER,
    4_138_311_608,
    "f205e77aa2ade168db6b09d4bc420d156141f64ab964278a9c181a2bdf2a232b",
)
os.environ["TRIBEV2_VIDEO_MODEL_PATH"] = str(VIDEO_ENCODER)
print("TRIBE video encoder ready:", VIDEO_ENCODER)

# Public 6 MB YOLO-nano weights; ungated, integrity-checked, and cached.
_yolo_path = Path(os.environ["YOLO_MODEL"])
_expected_yolo_sha = "''' + YOLO_SHA256 + '''"
if not _yolo_path.exists() or hashlib.sha256(_yolo_path.read_bytes()).hexdigest() != _expected_yolo_sha:
    urllib.request.urlretrieve("''' + YOLO_URL + '''", _yolo_path)
_actual_yolo_sha = hashlib.sha256(_yolo_path.read_bytes()).hexdigest()
assert _actual_yolo_sha == _expected_yolo_sha, (
    f"YOLO weight checksum mismatch: {_actual_yolo_sha}"
)
from ultralytics import YOLO
YOLO(os.environ["YOLO_MODEL"])
print("YOLO ready:", os.environ["YOLO_MODEL"])

# ---- start the API and keep a durable log --------------------------------
# A notebook rerun should replace only its own stale scorer/tunnel processes.
subprocess.run(["pkill", "-f", "uvicorn server:app"], check=False)
subprocess.run(["pkill", "-f", "/content/cloudflared tunnel"], check=False)
time.sleep(2)
LOG_PATH = Path("/content/neuro-server.log")
api_log = open(LOG_PATH, "w", buffering=1)
api = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app",
                        "--host", "0.0.0.0", "--port", "8000"],
                       cwd="/content", stdout=api_log, stderr=subprocess.STDOUT,
                       text=True)

def show_log_tail(lines=80):
    api_log.flush()
    if LOG_PATH.exists():
        print("\\n--- neuro-server.log (tail) ---")
        print("\\n".join(LOG_PATH.read_text(errors="replace").splitlines()[-lines:]))
        print("--- end log ---\\n")

print("Loading TRIBE v2 (checkpoint is ~676 MB)...")
health = None
for _ in range(180):  # up to 30 minutes for a cold Colab cache
    if api.poll() is not None:
        show_log_tail()
        raise RuntimeError(f"API process exited with code {api.returncode}")
    try:
        health = json.load(urllib.request.urlopen("http://localhost:8000/health", timeout=5))
        if health.get("startup_error"):
            show_log_tail()
            raise RuntimeError("TRIBE startup failed: " + json.dumps(health["startup_error"], indent=2))
        if health.get("ready"):
            break
    except RuntimeError:
        raise
    except Exception:
        pass
    time.sleep(10)
else:
    show_log_tail()
    raise TimeoutError("TRIBE did not become ready within 30 minutes")

print("READY:", json.dumps(health, indent=2))

# ---- public tunnel via cloudflared (no account needed) -------------------
if not Path("/content/cloudflared").exists():
    urllib.request.urlretrieve(
        "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
        "/content/cloudflared")
    os.chmod("/content/cloudflared", 0o755)
tun = subprocess.Popen(["/content/cloudflared", "tunnel", "--url", "http://localhost:8000",
                        "--no-autoupdate"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
public_url = None
for line in tun.stdout:
    print(line, end="")
    m = re.search(r"https://[-a-z0-9]+\\.trycloudflare\\.com", line)
    if m:
        public_url = m.group(0); break

print("\\n" + "=" * 64)
print("NEURO_API_URL =", public_url)
print("NEURO_API_KEY =", os.environ["NEURO_API_KEY"])
print("=" * 64)
print("Paste both into the Pika neuro-eval / neuro-optimize skill.")
print("Text mode:", health["text_reason"])
print("Server log:", LOG_PATH)
'''

SCORE = '''\
# 5. Extract the activation / engagement score for a clip.
#    Hits the LOCAL server (localhost:8000), so the cloudflared 100s edge
#    timeout never applies — a long first call still returns here.
import os, json, requests
from pathlib import Path

# Set VIDEO to a URL/path, or leave it as None to upload from your computer.
VIDEO = None
if VIDEO is None:
    from google.colab import files
    uploaded = files.upload()
    if not uploaded:
        raise RuntimeError("No video uploaded")
    filename, content = next(iter(uploaded.items()))
    path = f"/content/{Path(filename).name}"
    Path(path).write_bytes(content)
elif VIDEO.startswith("http"):
    path = "/content/clip.mp4"
    download = requests.get(VIDEO, timeout=120)
    download.raise_for_status()
    Path(path).write_bytes(download.content)
else:
    path = VIDEO

print("Scoring on the T4 (first inference is slowest — encoder warm-up)…")
with open(path, "rb") as clip:
    r = requests.post("http://localhost:8000/score",
                      headers={"x-api-key": os.environ["NEURO_API_KEY"]},
                      files={"video": (Path(path).name, clip, "video/mp4")},
                      timeout=1800)
if not r.ok:
    try:
        error_body = r.json()
    except Exception:
        error_body = r.text
    print("SCORING FAILED:", r.status_code, json.dumps(error_body, indent=2))
    try:
        d = requests.get("http://localhost:8000/diagnostics",
                         headers={"x-api-key": os.environ["NEURO_API_KEY"]},
                         timeout=30).json()
        print("DIAGNOSTICS:", json.dumps(d, indent=2))
    except Exception as diag_exc:
        print("Could not fetch diagnostics:", diag_exc)
    log_path = Path("/content/neuro-server.log")
    if log_path.exists():
        print("\\nSERVER LOG (last 120 lines):")
        print("\\n".join(log_path.read_text(errors="replace").splitlines()[-120:]))
    raise RuntimeError(f"Scoring failed at HTTP {r.status_code}; details printed above")
rep = r.json()

peak, weak = rep.get("peak", {}), rep.get("weakWindow", {})
print("\\n" + "=" * 62)
print(f"AD SCORE: {rep['adScore']}/100   ({rep['duration']}s)")
print("-" * 62)
for x in rep["regions"]:                      # per-region cortical activation
    print(f"  {x['short']:5} {x['name']:26} {x['score']:5.1f}   ({x['reliability']})")
print("-" * 62)
print(f"  Peak  {peak.get('time')}s  ({peak.get('label')})  {peak.get('value')}")
print(f"  Weak  {weak.get('startTime')}–{weak.get('endTime')}s  mean {weak.get('meanValue')}  <- regenerate this beat")
print(f"  Modalities: {', '.join(rep['inference']['modalities'])}"
      f" | text_used={rep['inference']['text_used']}")
print("  Feature contributions:")
for feature in rep["adScoreBreakdown"]["features"]:
    score = feature["score"]
    if score is not None:
        print(f"    {feature['name']:27} score={score:5.1f} "
              f"weight={feature['effective_weight']:.3f} "
              f"contribution={feature['contribution']:.2f}")
print("  Next:", rep["rewardFeedback"]["generator_instruction"])
for warning in rep["inference"].get("warnings", []):
    print("  Warning:", warning)
print("=" * 62)
# rep['global'] = per-frame engagement curve; rep['cognitiveSeries'] = per-family traces.
'''

TRAIN_LOOP = '''\
# 6. Bounded optimization/training loop (maximum 5 candidates, 3 seconds each).
#
# Upload candidates in iteration order. The API normalizes every upload to <=3s,
# scores it with TRIBE activations + OpenCV features, keeps only improvements,
# and stops after two sub-epsilon gains. This is a reward/selection loop; Pika's
# neuro-optimize skill can generate each next candidate from the prior diagnosis.
import json, os, requests
from google.colab import files

MAX_ITERATIONS = 5
EPSILON = 0.5
print(f"Upload 1–{MAX_ITERATIONS} candidate videos in iteration order.")
uploaded = files.upload()
if not uploaded:
    raise RuntimeError("No candidates uploaded")
if len(uploaded) > MAX_ITERATIONS:
    raise ValueError(f"Upload at most {MAX_ITERATIONS} candidates")

multipart = [
    ("videos", (name, content, "video/mp4"))
    for name, content in uploaded.items()
]
r = requests.post(
    "http://localhost:8000/train-loop",
    headers={"x-api-key": os.environ["NEURO_API_KEY"]},
    data={"max_iterations": MAX_ITERATIONS, "epsilon": EPSILON},
    files=multipart,
    timeout=1800,
)
if not r.ok:
    print(json.dumps(r.json(), indent=2))
    r.raise_for_status()
loop = r.json()
print("\\nBEST ITERATION:", loop["best_iteration"], "SCORE:", loop["best_score"])
print("STOP:", loop["stop_reason"])
for item in loop["history"]:
    marker = "✓" if item["accepted"] else "×"
    print(
        f"{marker} iter {item['iteration']}: {item['candidate']} "
        f"score={item['score']} activation={item['activation_score']} "
        f"visual={item['visual_score']} reward={item['reward']}"
    )
    print("   next:", item["next_action"])
'''

nb = {
    "cells": [
        md("# Cerebra · TRIBE v2 neuro-engagement server (Colab)\\n\\n"
           "Hosts `facebook/tribev2` as a public HTTP scoring endpoint for the Pika "
           "**neuro-eval** / **neuro-optimize** skills.\\n\\n"
           "**Setup:** `Runtime → Change runtime type → T4 GPU`. A Hugging Face "
           "`HF_TOKEN` with accepted LLaMA 3.2 access is optional: when unavailable, "
           "the server uses TRIBE's ungated video pathway. Set "
           "`TRIBEV2_MODALITIES=video,audio` to add Wav2Vec-BERT. "
           "Run the cells top to bottom. **The install cell restarts the kernel on "
           "purpose** — when it reconnects, continue from the *Verify* cell (do not "
           "re-run install). The last cell prints the URL + key to paste into the skill."),
        md("## 1. Install packages\\n"
           "This cell installs everything, then **auto-restarts the kernel** so the "
           "pinned NumPy loads against a clean process (avoids the `numpy.ufunc ... "
           "__module__` ABI crash). The session will say *Reconnecting* — that's expected."),
        code(INSTALL),
        md("## 2. Verify (run after the restart)"),
        code(VERIFY),
        md("## 3. Write the scorer, OpenCV/YOLO features, ad-score formula, and API\\n"
           "These are the exact source files from the plugin's `server/` folder."),
        code("%%writefile /content/engagement.py\n" + ENGAGEMENT),
        code("%%writefile /content/video_features.py\n" + VIDEO_FEATURES),
        code("%%writefile /content/ad_score.py\n" + AD_SCORE),
        code("%%writefile /content/server.py\n" + SERVER),
        md("## 4. Launch + tunnel\\nThis starts a logged API process, verifies model readiness, "
           "then creates the public tunnel. The cell may finish; the subprocesses stay alive "
           "for the Colab runtime. Wait for `READY` before scoring."),
        code(LAUNCH),
        md("## 5. Score a clip → activation score\\nRuns against `localhost:8000` (no tunnel timeout). "
           "Upload a video (or set `VIDEO` to a URL/path). It is normalized to at most 3 seconds, "
           "then scored using a transparent linear combination of TRIBE, OpenCV, and YOLO features. On failure this prints the structured stage, "
           "diagnostics, and server-log tail instead of hiding everything behind `HTTPError: 500`."),
        code(SCORE),
        md("## 6. Run up to five optimization iterations\\n"
           "Upload 1–5 candidate clips. Each is trimmed to ≤3 seconds; the loop keeps "
           "the best score, never regresses, and stops on a two-iteration plateau."),
        code(TRAIN_LOOP),
    ],
    "metadata": {
        "accelerator": "GPU",
        "colab": {"provenance": [], "gpuType": "T4"},
        "kernelspec": {"display_name": "Python 3", "name": "python3"},
        "language_info": {"name": "python"},
    },
    "nbformat": 4,
    "nbformat_minor": 0,
}

out = ROOT / "colab" / "cerebra_tribev2_server.ipynb"
out.write_text(json.dumps(nb, indent=1))
print("wrote", out)
