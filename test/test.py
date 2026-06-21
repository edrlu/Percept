"""Run TRIBE v2 inference on the local test clip."""

import subprocess

import torch
from tribev2 import TribeModel


# TRIBE v2 currently invokes WhisperX with float16 even after selecting CPU.
# float16 is not supported by the CPU backend on this Mac, so replace only
# that subprocess argument with WhisperX's CPU-safe int8 mode.
if not torch.cuda.is_available():
    _subprocess_run = subprocess.run

    def _run_whisperx_on_cpu(command, *args, **kwargs):
        if isinstance(command, list) and command[:2] == ["uvx", "whisperx"]:
            command = command.copy()
            option = command.index("--compute_type")
            command[option + 1] = "int8"
        return _subprocess_run(command, *args, **kwargs)

    subprocess.run = _run_whisperx_on_cpu

device = "cuda" if torch.cuda.is_available() else "cpu"

# ``TribeModel.from_pretrained`` applies its ``device`` argument to the brain
# model, but the pretrained config leaves feature extractors on CUDA.  On a
# CPU-only machine Neuralset hides that CUDA error behind the generic
# "Model loading went wrong" exception.  Keep every model in this smoke test
# on the selected device.
model = TribeModel.from_pretrained(
    "facebook/tribev2",
    cache_folder="./cache",
    device=device,
    config_update={
        "data.text_feature.device": device,
        "data.audio_feature.device": device,
        "data.video_feature.image.device": device,
    },
)

df = model.get_events_dataframe(video_path="../downloads/cc2.mp4")
preds, segments = model.predict(events=df)
print(preds.shape)  # (n_timesteps, n_vertices)
