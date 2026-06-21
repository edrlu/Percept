"""Optional speedups for the V-JEPA2 video encode loop (opt-in via env vars).

Profiling (nvidia-smi dmon) showed the stock neuralset encode loop
(`HuggingFaceVideo._get_data`) is two costs in series, with no overlap:

  per window k:  decode 64 frames on CPU (moviepy seeks)  ->  one batch-1
                 V-JEPA2-giant forward on the GPU

The GPU pins to 100% during the forward but sits idle ~30% of the wall-clock
waiting on the serial CPU decode, and batch-1 forwards underutilize the A100
(~40% MFU). This module monkeypatches that loop -- ONLY for vjepa2, ONLY when
explicitly opted in -- to attack both:

  TRIBEV2_BATCH=N   : run N windows' forwards as ONE batched forward. Better GPU
                      MFU on the dominant cost. Per-item independent (V-JEPA2 is
                      layernorm-based, no cross-batch ops), so outputs match the
                      stock path to fp tolerance -- exactly equal on CPU; on GPU
                      a different batch size can pick a different GEMM/attention
                      kernel, so expect ~1e-6 differences, not bit-identity.
  TRIBEV2_PREFETCH=1: decode the next batch on a background thread while the GPU
                      runs the current batch's forward. Reclaims the ~30% idle.
                      Same frames in the same order, only rescheduled (decode is
                      pure CPU and does not affect the numbers).

Both default OFF. When neither is requested, `install_fastpath()` is a no-op and
the stock neuralset loop runs completely untouched (zero risk to the default
scientific path). The windowing, frame sampling, max_imsize resize, and the
per-window token/layer aggregation below are copied VERBATIM from
neuralset.extractors.video so the optimized path matches the stock path to fp
tolerance. Validate by A/B-ing the engagement score with TRIBEV2_BATCH=1 vs >1
(and PREFETCH on/off) on the same clip -- they should agree.

Note: the optimized path bypasses neuralset's exca infra caching decorator on
`_get_data`. That cache is redundant with the worker's own per-video JSON cache
(content-addressed), so losing it only matters within a single uncached run.
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import torch
from tqdm import tqdm

from neuralset import base as nsbase
from neuralset.extractors.image import _fix_pixel_values
from neuralset.extractors.video import (
    HuggingFaceVideo,
    _HFVideoModel,
    _VideoImage,
)

logger = logging.getLogger(__name__)

# Saved reference to the stock (exca-decorated) method so we can delegate to it
# for anything we don't optimize (non-vjepa2 models).
_ORIGINAL_GET_DATA = None


def _opts():
    """Read the opt-in knobs. batch_size>=1, prefetch bool."""
    batch_size = max(1, int(os.getenv("TRIBEV2_BATCH", "1")))
    prefetch = os.getenv("TRIBEV2_PREFETCH", "0") not in ("", "0", "false", "False")
    return batch_size, prefetch


def _predict_hidden_states_batch(model, batch_datas):
    """Batched equivalent of _HFVideoModel.predict + predict_hidden_states for vjepa2.

    Mirrors the stock per-window path exactly, but stacks B clips into a single
    forward. Each clip is run through the processor individually (the known-good
    path), then the resulting tensors are concatenated on the batch axis -- so we
    never rely on the processor accepting multiple videos at once.

    batch_datas: list[np.ndarray], each (num_frames, H, W, C).
    returns: torch.Tensor of shape (B, L, tokens, dim)  (L = #hidden states)
    """
    per_clip = []
    for data in batch_datas:
        # vjepa2 branch of _HFVideoModel.predict: field="videos", no "text".
        inputs = model.processor(videos=list(data), return_tensors="pt")
        _fix_pixel_values(inputs)  # prevent NaNs on uniform frames (as in stock)
        per_clip.append(inputs)

    merged = {}
    for key in per_clip[0].keys():
        vals = [inp[key] for inp in per_clip]
        if isinstance(vals[0], torch.Tensor):
            # Each clip contributes one item on the batch axis (e.g.
            # pixel_values_videos: (1, num_frames, C, H, W) -> (B, ...)).
            merged[key] = torch.cat(vals, dim=0).to(model.model.device)
        else:
            # Non-tensor processor outputs are clip-invariant for vjepa2; keep one.
            merged[key] = vals[0]

    with torch.inference_mode():
        pred = model.model(**merged)
    # predict_hidden_states (non-xclip branch): stack every hidden state on a new
    # axis 1 -> (B, L, tokens, dim). Equivalent to the stock batch-1 path up to fp
    # tolerance (GPU kernel choice can vary by batch size), just with B>1.
    states = pred.hidden_states
    out = torch.cat([x.unsqueeze(1) for x in states], dim=1)
    return out


def _patched_get_data(self, events):
    """Drop-in for HuggingFaceVideo._get_data with batched + prefetched encoding.

    Falls back to the stock method for non-vjepa2 models. The vjepa2 setup and
    windowing are copied verbatim from neuralset.extractors.video._get_data.
    """
    batch_size, prefetch = _opts()

    # Only optimize native vjepa2 video extraction; everything else -> stock.
    if "vjepa2" not in self.image.model_name or (batch_size == 1 and not prefetch):
        yield from _ORIGINAL_GET_DATA(self, events)
        return

    logger.info(
        "[vjepa-fastpath] active: batch=%s prefetch=%s", batch_size, prefetch
    )

    # --- setup: VERBATIM from _get_data (video.py:257-270) ---
    model = _HFVideoModel(
        model_name=self.image.model_name,
        pretrained=self.image.pretrained,
        layer_type=self.layer_type,
        num_frames=self.num_frames,
    )
    if model.model.device.type == "cpu":
        model.model.to(self.image.device)
    freq = events[0].frequency if self.frequency == "native" else self.frequency
    T = 1 / freq if self.clip_duration is None else self.clip_duration
    subtimes = list(
        k / model.num_frames * T for k in reversed(range(model.num_frames))
    )

    for event in events:
        video = event.read()
        # NOTE: vjepa2's forward ignores audio (only Phi-4 consumes it), so we do
        # not slice audio here -- it would be computed and discarded.
        freq = self.frequency if self.frequency != "native" else event.frequency
        expect_frames = nsbase.Frequency(freq).to_ind(event.duration)
        times = np.linspace(0, video.duration, expect_frames + 1)[1:]
        output = np.array([])

        def decode_window(t):
            # VERBATIM frame sampling + optional resize (video.py:290, 294-301).
            ims = [_VideoImage(video=video, time=max(0, t - t2)) for t2 in subtimes]
            pil_imgs = [i.read() for i in ims]
            if pil_imgs and self.max_imsize is not None:
                factor = max(pil_imgs[0].size) / self.max_imsize
                if factor > 1:
                    size = tuple(int(s / factor) for s in pil_imgs[0].size)
                    pil_imgs = [pi.resize(size) for pi in pil_imgs]
            return np.array([np.array(pi) for pi in pil_imgs])

        def decode_batch(idxs):
            return [decode_window(times[k]) for k in idxs]

        def write_outputs(idxs, out, output):
            # Stock asserts the forward returned exactly one item before [0];
            # here the batch dim must equal the number of windows in this batch.
            if out.shape[0] != len(idxs):
                raise RuntimeError(
                    f"V-JEPA2 batch mismatch: forward returned {out.shape[0]} "
                    f"items for {len(idxs)} windows"
                )
            # Per-item aggregation: VERBATIM from video.py:305-312.
            for bi, k in enumerate(idxs):
                t_embd = out[bi]  # (L, tokens, dim) -- aggregate_tokens wants non-batched
                embd = self.image._aggregate_tokens(t_embd).cpu().numpy()
                if (
                    not self.image.cache_all_layers
                    and self.image.cache_n_layers is None
                ):
                    embd = self.image._aggregate_layers(embd)
                if not output.size:
                    output = np.zeros((len(times),) + embd.shape)
                    logger.debug("Created Tensor with size %s", output.shape)
                output[k] = embd
            return output

        batches = [
            list(range(i, min(i + batch_size, len(times))))
            for i in range(0, len(times), batch_size)
        ]

        pbar = tqdm(total=len(times), desc="Encoding video")
        # max_workers=1 is LOAD-BEARING: the moviepy reader (one ffmpeg pipe +
        # mutable seek state) is not thread-safe, so all decodes must run on a
        # single worker thread. Do NOT raise this above 1.
        executor = ThreadPoolExecutor(max_workers=1) if prefetch else None
        pending = None  # in-flight prefetch decode, tracked so we can drain it
        try:
            if prefetch and batches:
                # Decode batch 0, then for each batch kick off the NEXT decode on
                # the worker thread before running this batch's GPU forward.
                pending = executor.submit(decode_batch, batches[0])
                for j, idxs in enumerate(batches):
                    batch_datas = pending.result()
                    pending = (
                        executor.submit(decode_batch, batches[j + 1])
                        if j + 1 < len(batches)
                        else None
                    )
                    out = _predict_hidden_states_batch(model, batch_datas)
                    output = write_outputs(idxs, out, output)
                    pbar.update(len(idxs))
            else:
                for idxs in batches:
                    batch_datas = decode_batch(idxs)
                    out = _predict_hidden_states_batch(model, batch_datas)
                    output = write_outputs(idxs, out, output)
                    pbar.update(len(idxs))
        finally:
            pbar.close()
            # Drain any in-flight prefetch so its exception isn't swallowed and it
            # isn't still reading the video while we tear the reader down.
            if pending is not None:
                pending.cancel()
                try:
                    pending.result()
                except Exception:  # already unwinding; just settle the future
                    pass
            if executor is not None:
                # Close the reader on the SAME worker thread that spawned the
                # ffmpeg subprocess (keeps spawn+seek+close on one thread), then
                # shut the pool down.
                try:
                    executor.submit(video.close).result()
                except Exception:
                    video.close()
                executor.shutdown(wait=True)
            else:
                video.close()

        # set first (time) dim to last -- VERBATIM (video.py:315).
        output = output.transpose(list(range(1, output.ndim)) + [0])
        yield nsbase.TimedArray(
            data=output.astype(np.float32),
            frequency=freq,
            start=nsbase._UNSET_START,
            duration=event.duration,
        )


def install_fastpath():
    """Install the monkeypatch IFF a speedup is requested. Returns True if active.

    No-op (and leaves neuralset untouched) when TRIBEV2_BATCH<=1 and
    TRIBEV2_PREFETCH is unset -- so the default path carries zero risk.
    """
    global _ORIGINAL_GET_DATA
    batch_size, prefetch = _opts()
    if batch_size == 1 and not prefetch:
        logger.info("[vjepa-fastpath] not requested; stock encode loop in use")
        return False
    if _ORIGINAL_GET_DATA is None:
        _ORIGINAL_GET_DATA = HuggingFaceVideo._get_data
        HuggingFaceVideo._get_data = _patched_get_data
    logger.info(
        "[vjepa-fastpath] installed (batch=%s prefetch=%s)", batch_size, prefetch
    )
    return True
