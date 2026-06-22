# Cerebra Neuro-Eval — a Pika plugin

> **The measurement layer Pika is missing.** Every Pika skill *generates* a video.
> This plugin tells you whether it will actually **land** — and then makes it
> better on its own.

Cerebra Neuro-Eval scores any video with Meta/FAIR's **TRIBE v2** brain model
(a population-average cortical-response predictor) and turns that into a
decision-ready **0–100 neuro-engagement score**, a per-second attention curve,
four cognitive channel traces, and the single weakest beat to fix. A second skill
wraps that score in an **automatic generate → score → fix → re-score loop**, so a
Pika video improves itself with a neural reward signal and no human in the inner
loop.

It's a drop-in: download the folder, point it at a TRIBE v2 endpoint, and the two
skills appear to the Pika agent.

---

## Why this exists (the gap)

Pika's skill library is entirely **generative** — `ugc-ads`, `app-sizzle`,
`founder-product-video`, `viral-hook`, … all end at *"here's your video."* There
is **no skill that evaluates or optimizes** a video (searching Pika's own skill
registry for "score / evaluate / will this perform / brain response" returns
nothing). So creators ship blind: no signal on what works, no loop to improve it.

This plugin closes that loop.

| | Before | With Neuro-Eval |
|---|---|---|
| After generating | "Looks good?" 🤷 | `61/100`, weak at 0:11–0:14, LANG dip |
| Picking a cut | gut feel | A/B scored, winner by margin |
| Improving | re-prompt and hope | auto loop, score climbs each round |

---

## What's in the box

```
pika-plugin/
├── skills/
│   ├── neuro-eval/SKILL.md       # score a video → engagement report
│   └── neuro-optimize/SKILL.md   # auto loop: generate → score → fix → re-score
├── server/
│   ├── engagement.py             # TRIBE v2 cortical tensor → 0–100 engagement
│   ├── server.py                 # FastAPI /score + /health
│   └── requirements.txt
├── colab/
│   ├── cerebra_tribev2_server.ipynb   # host TRIBE v2 on a free T4 + public tunnel
│   └── _build_notebook.py             # regenerates the notebook from server/
├── client/
│   └── cerebra_eval.py           # stdlib CLI: score / A-B a video locally
└── .env.example
```

---

## Setup (≈5 min)

### 1. Host the brain model (Colab, free T4)
TRIBE v2 needs a GPU, so the weights live on Colab — not on the Pika host.

1. Open `colab/cerebra_tribev2_server.ipynb` in Google Colab.
2. `Runtime → Change runtime type → T4 GPU`.
3. Optional: add a Hugging Face read token to Colab **Secrets** as `HF_TOKEN`
   after accepting access to `meta-llama/Llama-3.2-3B`. Without one, the server
   uses TRIBE's ungated video pathway instead of failing. The notebook downloads
   and verifies V-JEPA2 before accepting requests, bypassing the `hf-xet` stall
   observed during real T4 testing.
4. Run all cells. The last cell prints:
   ```
   NEURO_API_URL = https://<random>.trycloudflare.com
   NEURO_API_KEY = <generated secret>
   ```
   Keep that cell running — it *is* your live endpoint.

### 2. Point the plugin at it
```bash
cp .env.example .env     # then paste in NEURO_API_URL + NEURO_API_KEY
export NEURO_API_URL=https://<random>.trycloudflare.com
export NEURO_API_KEY=<secret>
curl -s "$NEURO_API_URL/health"      # {"ready": true, "cuda": true, ...}
```

`TRIBEV2_TEXT_MODE` controls the gated language path:

- `auto` (default): use text when authorized; retry with the configured ungated
  modalities on failure.
- `off`: reliable ungated scoring for any user.
- `required`: require WhisperX + LLaMA and return a clear diagnostic if unavailable.

`TRIBEV2_MODALITIES` controls the ungated base path:

- `video` (Colab default): one public 4.14 GB V-JEPA2 encoder; most reliable on T4.
- `video,audio`: also download/use the 2.32 GB Wav2Vec-BERT encoder.

The notebook pins the tested TRIBE v2 Git revision, logs the API to
`/content/neuro-server.log`, and exposes authenticated `/diagnostics`. A failed
score prints the exact stage, hint, diagnostics, and log tail instead of only
`HTTPError: 500`.

### Debugged Colab failure

The original HTTP 500/hang had four independent causes:

1. Colab retained a Torchaudio build with a different Torch ABI.
2. dependency resolution replaced TRIBE's exact NumPy 2.2.6 pin.
3. `/health` reported ready before the lazily loaded V-JEPA2 encoder was present.
4. `hf-xet` transferred roughly 3 GB, then deadlocked after a CDN request failed,
   leaving `/score` blocked until its 30-minute client timeout.

The notebook now pins the compatible Torch/Torchaudio/NumPy stack, downloads
V-JEPA2 before server startup via resumable `curl`, verifies its exact byte size
and SHA-256, and defaults to video-only TRIBE inference so Wav2Vec-BERT is not a
hidden 2.32 GB dependency. A real T4 run completed `/score` and a three-candidate
plateau loop using one reused clip.

### 3. Install the skills into Pika
Drop `skills/neuro-eval` and `skills/neuro-optimize` into your Pika plugins source
(the same place the official `Pika-Labs/Pika-Plugins` skills load from). The Pika
agent will then discover them via `search_skill` ("score this video",
"make this video better", …).

---

## Use it

**Score (quick check, no Pika needed):**
```bash
python client/cerebra_eval.py myclip.mp4
python client/cerebra_eval.py cutA.mp4 cutB.mp4    # A/B → names a winner
```

**In Pika (natural language):**
- *"Score this video"* → runs **neuro-eval**, returns the report.
- *"Make this ad more engaging"* → runs **neuro-optimize**, iterates until the
  score plateaus, returns the winning clip + the score history.

---

## The score, briefly
TRIBE v2 predicts average cortical response on the fsaverage5 surface. We read
four engagement families off the Glasser/HCP-MMP atlas — **auditory, language,
attention, visual** — and measure their activation relative to the full cortex
at each timestep. The canonical `adScore` is a transparent weighted sum: 55%
TRIBE cortical features, 30% OpenCV production features, and 15% YOLO
semantic/composition features. Every response includes raw values, normalized
subscores, weights, and contributions. Every upload is normalized to at most
three seconds. The weakest weighted features and explicit generator instruction
become the reward feedback for a maximum-five-iteration Pika optimization loop.

It is a **prediction of population-average response**, not a measurement of any
individual viewer and not a sales guarantee — a strong, fast, directional signal
for the inner loop, with an optional human panel to confirm the final pick.

## Provenance
The scoring math is ported from Cerebra's production TRIBE v2 worker; this plugin
packages it as a standalone, Pika-installable evaluation + optimization layer.
Model: [`facebook/tribev2`](https://huggingface.co/facebook/tribev2).

> **License note:** the plugin code and bundled YOLO/OpenCV path are separate
> from the TRIBE v2 weights. Meta publishes TRIBE v2 under CC-BY-NC-4.0.
> Commercial ad-scoring use requires permission compatible with that license.
