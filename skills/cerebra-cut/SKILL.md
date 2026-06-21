---
name: cerebra-cut
description: >-
  Predict a video's population-average cortical-ENGAGEMENT curve with Meta's TRIBE v2
  (via the bundled Cerebra MCP), find the peak-engagement moments, then auto-cut a
  highlight — trimmed to the strongest beats, captioned, ready to post. The Cerebra MCP
  supplies the neural prediction; the Pika MCP does the editing. Requires the cerebra
  plugin (TRIBE v2 backend) AND the pika plugin installed.
  Triggers: "where does my video peak", "cut my video to the best part", "neural highlight
  reel", "score my video's engagement", "auto-edit the most engaging moments", "find the
  most engaging clip".
argument-hint: "[video path or URL] [optional: # of clips or target seconds]"
required-capabilities:
  - mcp__plugin_cerebra_cerebra__predict_engagement
  - mcp__plugin_cerebra_cerebra__engagement_health
  - mcp__plugin_pika_pika__upload_asset
  - mcp__plugin_pika_pika__transcribe_audio
  - mcp__plugin_pika_pika__edit_trim
  - mcp__plugin_pika_pika__edit_concat
  - mcp__plugin_pika_pika__add_captions
  - mcp__plugin_pika_pika__task_status
---

# Cerebra Cut

Turns a video into a highlight by **predicting where it is most engaging** and cutting to
those moments. Cerebra's bundled MCP runs Meta's `facebook/tribev2` model to produce a
population-average cortical-engagement curve over time, broken down into four engagement
dimensions, with ranked peak moments. This skill reads that curve, picks the strongest
beats, and renders the cut with the Pika edit tools.

**Two backends, one workflow:**
- `mcp__plugin_cerebra_cerebra__predict_engagement` — the TRIBE v2 prediction (runs
  **locally**; reads a local file path or downloads an https URL).
- `mcp__plugin_pika_pika__*` — uploading, trimming, concatenating, captioning (runs in the
  **cloud**; needs an https URL).

## Scientific honesty (do not skip)

TRIBE v2 predicts **population-average cortical responses** to naturalistic video. The four
dimensions are **manually defined cortical surface proxies**, not measurements of emotion,
reward, intent, memory, or any individual viewer's mind. When you report results, call this
a *predicted engagement* signal — never "this is what the brain does" or "this measures how
people feel." See `references/engagement-rubric.md`.

## Workflow

### Step 0 — Intake (empty args)

If no video was provided, print this verbatim and STOP — do not call any tool:

> **What would you like to cut to its most engaging moments?** Paste any of:
> - **A local video path** — e.g. `/Users/me/Desktop/clip.mp4`
> - **An https URL** to a video
> - **A path + intent** — e.g. `clip.mp4 — one 15s highlight` or `talk.mp4 — top 3 moments`

Wait for the next message. Don't guess an input.

### Step 1 — Predict the engagement curve (Cerebra MCP)

Call `mcp__plugin_cerebra_cerebra__predict_engagement({ video, top_peaks })` with the
**local path or https URL exactly as the user gave it** (this MCP runs locally and reads it
directly — do NOT upload to Pika first). Set `top_peaks` to a few more than you intend to
use (default 5).

> First call is slow — the TRIBE model loads on demand. If you want to confirm the backend
> is alive first, call `mcp__plugin_cerebra_cerebra__engagement_health` (it does not trigger
> a load).

You get back: `duration`, `frames`, `tr`, `global` (per-frame 0–100 engagement), `regions`
(the four dimensions, strongest first), `peaks` (ranked `{rank, center_s, start_s, end_s,
score, dimension, label}`), and `peak` (the single strongest moment).

### Step 2 — Choose the cut

Pick a recipe from `references/edit-recipes.md` based on the user's intent and `duration`:
- **Single highlight** (default) — the top peak's `[start_s, end_s]`, optionally widened to
  a target length.
- **Top-N reel** — the top N non-overlapping peaks in chronological order, concatenated.
- **Tighten** — keep the whole video but drop the lowest-engagement stretches.

Map each chosen peak's `start_s`/`end_s` to trim ranges. If the user gave a target length,
expand/center each window to match (clamp to `[0, duration]`).

### Step 3 — Get a Pika URL for editing

The Pika edit tools need an https URL. If the input was a **local file**, call
`mcp__plugin_pika_pika__upload_asset` and use the returned `public_url`. If it was already
an https URL, reuse it. (Prediction in Step 1 already happened on the original input.)

### Step 4 — Render (Pika MCP)

- One range → `mcp__plugin_pika_pika__edit_trim` on that `[start_s, end_s]`.
- Multiple ranges → `edit_trim` each, then `mcp__plugin_pika_pika__edit_concat` in
  chronological order.
- Then `mcp__plugin_pika_pika__add_captions` (style `hormozi` or `tiktok` for social;
  `classic` for talks) so the cut is post-ready.
- If a tool returns `{task_id, status}`, poll `mcp__plugin_pika_pika__task_status(task_id)`
  in a tight loop until terminal. If it returns `cost_confirmation_required`, surface the
  estimate to the user and re-call with the `confirm_token` once approved.

### Step 5 — Deliver

Present the cut and a short engagement read:
- `[[video:<final_url>]]`
- Peak moment: `peak.time`s, dominant dimension `peak.label` (score `peak.value`).
- The ranked peaks you cut to (time + dimension + score).
- A one-line reminder that this is a *predicted* population-average engagement signal, not a
  measurement.

## What NOT to do

- **Don't upload to Pika before predicting.** `predict_engagement` reads the local
  path/URL directly; uploading first loses that and wastes a round-trip.
- **Don't overclaim.** No "reads minds / measures feelings / detects emotion." Predicted
  cortical-engagement proxy only.
- **Don't re-implement engagement scoring with Pika analysis tools.** The whole point of
  this plugin is the real TRIBE v2 prediction — if peaks look off, adjust `top_peaks` or the
  window, don't swap in `analyze_clip_highlights`.
- **Don't bake captions before trimming** — trim/concat first, caption the final cut.
