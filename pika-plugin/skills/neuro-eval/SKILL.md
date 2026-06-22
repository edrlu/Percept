---
name: neuro-eval
description: >-
  Score any video for predicted audience engagement using Meta's TRIBE v2 brain
  model. Returns a 0-100 neuro-engagement score, a per-second engagement curve,
  four cognitively-labelled traces (auditory, language, attention, visual), the
  peak moment, and the single weakest window to fix. This is the missing
  measurement layer: every other Pika skill GENERATES a video — this one tells you
  whether it will actually land, and why, before you publish or spend on ads. Use
  when the user asks to "score this video", "will this ad perform", "rate my
  video", "neuro test", "brain score", "attention curve", "which cut is better",
  "A/B two videos", "find the boring part", or "evaluate this clip". Pairs with
  neuro-optimize (which uses this score in an automatic improvement loop). Needs a
  running TRIBE v2 endpoint (see the plugin's colab/ notebook) — config in
  NEURO_API_URL / NEURO_API_KEY.
---

# neuro-eval — predicted audience engagement from a brain model

## What this does
Sends a video to a hosted **TRIBE v2** model (Meta/FAIR's population-average
cortical-response predictor) and turns its raw cortical output into a compact,
decision-ready report:

- **`adScore`** — canonical 0-100 reward score: a transparent linear
  combination of TRIBE cortical, OpenCV production, and YOLO semantic/composition
  features. `engagementScore` remains as a backward-compatible alias.
- **`activationScore`** — TRIBE-only activation relative to the full cortex.
- **`videoFeatures`** — OpenCV duration, dimensions, exposure, contrast,
  saturation, sharpness, motion energy, cut rate, and visual score. Uploads are
  normalized to at most 3 seconds.
- **`yoloFeatures`** — detection coverage, primary-subject area/confidence,
  centering, object count, and detected classes.
- **`adScoreBreakdown.features`** — every raw value, normalized score, base
  weight, effective weight, and weighted contribution.
- **`global`** — the per-frame engagement curve (the attention timeline).
- **`regions`** — four cognitive families, each 0-100 + its own trace:
  `AUD` (auditory/speech-music), `LANG` (language/message),
  `ATTN` (attention+salience), `VIS` (visual/motion).
- **`peak`** — the strongest moment `{time, label, value}`.
- **`weakWindow`** — the lowest-engagement span `{startTime, endTime, meanValue}`:
  *the beat to regenerate first.*
- **`rewardFeedback`** — the weakest region plus a concrete generator
  instruction for the next Pika candidate.

It is a **prediction of population-average cortical response**, not a measurement
of any individual or a guarantee of sales. Report it as a directional signal.

## Prerequisites (one-time)
A reachable TRIBE v2 endpoint. The fastest path is the bundled Colab notebook
`colab/cerebra_tribev2_server.ipynb` — run it on a free **T4 GPU**; it prints a
public `NEURO_API_URL` and `NEURO_API_KEY`. A Hugging Face token is optional:
the default `TRIBEV2_TEXT_MODE=auto` uses the gated LLaMA text encoder when
authorized and otherwise scores with TRIBE's configured ungated modalities.
The bundled Colab defaults to `TRIBEV2_MODALITIES=video`; `video,audio` adds
Wav2Vec-BERT. Set both
endpoint values in the environment:

```bash
export NEURO_API_URL="https://<your-tunnel>.trycloudflare.com"
export NEURO_API_KEY="<key printed by the notebook>"   # optional but recommended
```

Confirm it is up before scoring:

```bash
curl -s "$NEURO_API_URL/health"      # -> {"ready": true, "cuda": true, ...}
```

## How to run it

### Step 1 — get the video file
- If the user already has a clip, use its local path.
- If they reference a Pika generation, download the result URL to a local `.mp4`
  first (TRIBE v2 needs the actual bytes, not a URL). The endpoint normalizes
  every upload to at most three seconds.

### Step 2 — call the scorer
```bash
curl -s -X POST "$NEURO_API_URL/score" \
  -H "x-api-key: $NEURO_API_KEY" \
  -F "video=@/path/to/clip.mp4" | tee neuro_report.json
```

The JSON is the report described above.

### Step 3 — read it back to the user
Lead with the headline number, then the *actionable* part — the weak window and
which cognitive channel is underperforming there:

```
NEURO-ENGAGEMENT: 61/100
  Peak  0:08  (ATTN) — 84
  Weak  0:11–0:14 — 38   ← the part to fix
  Channels: ATTN 70 · AUD 64 · VIS 58 · LANG 52
Read: strong attention spike at the mid-reveal, but the close (0:11–0:14) drops
hard — mostly a LANG/VIS dip. The ending isn't earning the payoff.
```

Guidance for interpretation:
- A family scoring low **where the global curve dips** is the lever — e.g. low
  `LANG` in the weak window ⇒ the message/voiceover isn't landing there; low
  `VIS` ⇒ the motion/visual interest stalls; low `AUD` ⇒ flat audio energy.
- `reliability` on each region (`high`/`medium`) is an honest confidence note
  (auditory/language are near TRIBE's noise ceiling; attention/visual are
  association-level). Surface it; don't hide a medium-confidence claim as fact.

## Comparing two videos (A/B)
Score each clip, then compare `adScore` and the curves. Call a winner
only if the gap is meaningful (≥ ~3 points) and note where each one wins on the
timeline (e.g. "B holds attention through the close; A peaks earlier but sags").

## Handing off to optimization
If the user wants the video *improved*, not just scored, switch to the
**neuro-optimize** skill — it wraps this exact endpoint in an automatic
generate → score → fix-the-weak-window → re-score loop.

## Failure handling
- `/health` not `ready` ⇒ the Colab is still downloading/verifying the V-JEPA2
  encoder, loading TRIBE, or the runtime died. The notebook does not expose the
  endpoint until the encoder and cortical atlas are ready.
- `401` ⇒ wrong/missing `x-api-key`.
- `500` responses now include `detail.stage`, `detail.message`, and
  `detail.hint`. Query authenticated `/diagnostics` for the last traceback.
- `event_preprocessing` with WhisperX errors ⇒ keep `TRIBEV2_TEXT_MODE=auto`
  (automatic ungated-modality retry) or install/repair `uvx`.
- `text_preflight` ⇒ the server is in `required` mode but `HF_TOKEN` does not
  have accepted access to `meta-llama/Llama-3.2-3B`.
- Decode errors ⇒ re-encode to SDR H.264/AAC MP4.
