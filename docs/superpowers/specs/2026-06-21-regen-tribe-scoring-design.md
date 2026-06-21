# Real TRIBE scoring of regenerated clips vs. the original

**Date:** 2026-06-21
**Branch:** `regen-tribe-scoring`
**Status:** design — pending user review before implementation plan

## Goal

When a user regenerates a spliced clip, Pika produces **3 new 5-second takes** of the
cut segment. Score each take **individually against the original**, using the TRIBE
v2 brain-encoding model, so the user can pick the **best of the 3**. Show the scores
in the existing "Choose a take" picker *before* the takes are presented as choices.
When the user selects a take, it replaces the video **and** the corresponding segment
of the original engagement graph and its scores. Also offer **Reject all**.

## Background: what already exists (unchanged)

The regeneration pipeline is fully built and works end-to-end:

- `app/page.tsx:596 regenerate()` fans out `VARIANT_COUNT = 3` (`app/page.tsx:40`) jobs
  for one slot, sharing a `runId`.
- `app/api/regenerate/route.ts` archives the original cut segment to
  `data/<runId>/floor.mp4` and queues `regen/<jobId>/job.json`.
- `worker/regen-worker.mjs` spawns a headless agent per take that calls Pika
  `generate_video`, downloads `clip.mp4`, and POSTs it to
  `app/api/regenerate/complete/route.ts`, which merges it back (`mergeReplace`) and
  archives `data/<runId>/take_<n>.mp4` (+ `.mp3`).
- The client polls `GET /api/regenerate?job=<id>` (`app/page.tsx:670`) and renders a
  "Choose a take" modal (`app/page.tsx:928-949`) with one card per take and a score
  badge (`app/page.tsx:940`).
- **The scorer is a stub:** `app/lib/regen.ts:253 scoreArchivedTakeMp3()` returns
  `50 + random(0..50)` and never touches TRIBE or the original.

The original full video was **already scored** through TRIBE during the initial
analysis: `runAnalysis` (`app/page.tsx:374`) → `POST /api/predict` →
`worker/app.py:479 /predict` → `build_response` → the `analysis` state
(`app/page.tsx:226`), which holds per-frame engagement for the whole original.

## Key model facts that drive the design

From `worker/app.py` and `facebook/tribev2` `config.yaml`:

1. **Within-clip normalization.** `build_response` z-scores each cortical vertex
   against *its own temporal mean within that one clip* (`worker/app.py:340-343`),
   mapping 0 SD → 50. The time-average of a z-score is ~0, so **every clip's
   `engagementScore` lands near 50**. Independent `/predict` scores therefore do
   **not** rank different clips — they only describe within-clip dynamics.
   Cross-video comparability is explicitly deferred (`worker/app.py:98-100`).

2. **4-second receptive field at a 1 Hz brain output.** The video pathway runs at
   2 Hz with a **4 s** clip window (`config.yaml` `video_feature.frequency: 2.0`,
   `clip_duration: 4.0`, `vjepa2-vitg-fpc64-256` → 64 frames), while the cortical
   prediction is **1 Hz** (`neuro.frequency: 1.0` → `TR = 1 s`, `main.py:147`). The
   per-frame arrays the worker returns (`global[]`, `regions[].values[]`) are on the
   1 Hz grid, so a 5 s clip yields ~5 samples.
   - **Consequence:** concatenating `[original | take]` and slicing at the seam (an
     earlier candidate approach) fails at 5 s, because the 4 s window blends the two
     clips across essentially the whole take. **Rejected for 5 s clips.**

These two facts mean the correct method is a **fixed reference**: z-score each take
against the *original's* per-vertex baseline, not its own and not a concat.

## Design

### Scoring method — fixed-reference, original as baseline

Per regenerate run:

1. **Reuse the original's reference (do not re-score it).** The worker already
   computes the original's per-vertex temporal mean/SD (`μ_orig`, `σ_orig`) inside
   `build_response` when the original was analyzed; it currently discards them.
   We **persist** `μ_orig`/`σ_orig` when any video is scored via `/predict`, keyed by
   the video's content hash. For the original, this happens during the analysis the
   user already ran.

2. **Score only the 3 takes.** A new worker endpoint runs `/predict` on each take
   (3 × 5 s V-JEPA2 encodes — the original is never re-encoded), takes the take's raw
   cortical predictions, and **z-scores them against the saved `μ_orig`/`σ_orig`**.

3. **Aggregate to the 4 factors.** Using the existing parcel-balanced family
   aggregation (`worker/app.py:345-368`), produce the 4 family ("factor") traces —
   Auditory (AUD), Language (LANG), Attention (ATTN), Visual/motion (VIS) — on the
   0–100 scale, but referenced to the original. Each factor score = mean over time.

4. **Per-take headline = average of the 4 factor scores** (= the original-referenced
   `engagementScore`). The original itself sits at ~50 on this scale by construction
   (it is its own reference); a take at 57 drives +7 over where the original sat.

5. **Best take = the take with the highest headline** (best overall across the 4
   factors). The picker badges the winner.

Why this satisfies the constraints:
- **Individual, vs. the same original:** all 3 takes use one shared `μ_orig`/`σ_orig`
  → directly comparable to each other and to the original → "best of 3" is `max`.
- **Fastest on 1 A100:** only the 3 takes hit the GPU; the original reference is
  reused. (Batching the 3 takes into one forward pass is a later optimization.)
- **Stable σ:** using the original's full-video σ (many frames) avoids the noisy
  ~5-frame estimate a floor-only reference would give.
- **Consistent graph splice:** take values are already on the original's reference,
  so splicing them into the original graph segment needs no re-normalization.

### Components

**Worker (`worker/app.py`)**
- Extend `/predict` (or its cache write) to persist `μ/σ` per scored video, keyed by
  content hash, into the existing `prediction_cache`.
- Add `POST /score_takes`: input = the 3 take videos (multipart) + a `referenceId`
  (the original's content hash). For each take: run predict → z-score raw predictions
  against the referenced `μ/σ` → 4 factor traces → factor scores + headline + per-frame
  series. If the reference is missing from cache, score the original once to backfill.
  Returns the per-take scores, per-frame factor/global series, and the best index.
- Reuse `build_response`'s family/parcel logic; the only change is the source of
  `μ/σ` (referenced instead of within-clip).

**Next API**
- Extend `/predict` response (`app/api/predict/route.ts`) to surface the original's
  `referenceId` so the client can store it on `analysis` and pass it back later.
- Add `POST /api/regenerate/score` (`maxDuration = 14400`, like predict) that takes
  the `runId` + `referenceId`, reads `data/<runId>/take_<n>.mp4`, forwards them to the
  worker `/score_takes`, caches the result to `data/<runId>/scores.json`, and returns
  the per-take scores + per-frame series + best index.
- Remove the `scoreArchivedTakeMp3` stub and its call in `complete/route.ts` (scoring
  is no longer per-take inside `/complete`).

**Frontend (`app/page.tsx`)**
- Trigger `/api/regenerate/score` once all 3 takes reach `done` (generated + merged),
  showing a "Scoring 3 takes…" state. No take is presented as a choice until scored.
- Extend `RegenVariant` (`app/page.tsx:37`) with `score`, `factors` (the 4 family
  scores), `series` (per-frame factor + global arrays for the graph splice), and a
  run-level `best` index.
- Picker (`app/page.tsx:928-949`): each card shows its headline + the 4-factor
  breakdown; the best take is badged. Header shows the average of the 3 take headlines
  (secondary). Replace the "Filler model grade" label.
- **Selection (`chooseVariant`, `app/page.tsx:655` → `replacePreviewWithRegeneratedVideo`,
  `:513`):** in addition to swapping in `final.mp4`, splice the chosen take's per-frame
  series into `analysis` over `[seg.start, seg.end]` (mapped to analysis frame indices,
  resampled to the segment frame count): replace `global[]`, each `regions[].values[]`,
  and `cognitiveSeries`, then recompute `regions[].score`, the overall `engagementScore`
  (`app/page.tsx:320`), and `peak`. The graph re-renders reactively.
- **Reject all:** a button in the picker header (`app/page.tsx:936`) that discards
  `regenJobs[key]`, closes the picker, and leaves the original untouched.

### Data shapes

`POST /api/regenerate/score` request: `{ runId, referenceId }`.
Response:
```jsonc
{
  "best": 1,                       // index of highest-headline take
  "average": 54.3,                 // mean of the 3 take headlines
  "takes": [
    {
      "takeIndex": 0,
      "score": 52.1,               // headline = mean of the 4 factors
      "factors": { "AUD": 50.4, "LANG": 55.0, "ATTN": 51.2, "VIS": 51.8 },
      "series": {                  // per-frame, 1 Hz, ~5 samples, for graph splice
        "global": [ ... ],
        "AUD": [ ... ], "LANG": [ ... ], "ATTN": [ ... ], "VIS": [ ... ]
      }
    }
    // ... 2 more
  ]
}
```

`worker POST /score_takes`: multipart `take_0..2` files + `referenceId`; returns the
same `takes`/`best` payload (without the Next-side caching).

## Caveats (accepted)

1. **Coarse sampling:** 1 Hz output → ~5 samples per 5 s take. Scores are real but
   low-resolution; this is inherent to the model at 5 s.
2. **Reference scope:** the original reference is the full original video's per-vertex
   μ/σ (stable), not the 5 s segment's. This is intentional (stability + graph
   consistency); it means "vs. the original" = vs. the original video's overall
   cortical baseline.
3. **Cache dependency:** if the original's μ/σ was evicted from `prediction_cache`,
   `/score_takes` backfills by scoring the original once (rare in a fresh session).

## Implementation constraint

`AGENTS.md`/`CLAUDE.md`: this is a modified Next.js — read the relevant guide in
`node_modules/next/dist/docs/` before writing any route code, and heed deprecations.

## Files touched

- **New:** `app/api/regenerate/score/route.ts`.
- **Worker:** `worker/app.py` (persist μ/σ on `/predict`; add `/score_takes`).
- **Edit:** `app/api/predict/route.ts` (surface `referenceId`), `app/lib/regen.ts`
  (remove stub + `RegenJob` cleanup; any concat helper is no longer needed),
  `app/api/regenerate/complete/route.ts` (drop the scorer call), `app/page.tsx`
  (scoring trigger, picker 4-factor display + best badge, `chooseVariant` graph
  splice, Reject all), minor `app/globals.css`.

## Decided

- Scoring: fixed-reference (original baseline), 3 takes only, 4 factors.
- Headline = average of the 4 factors; best = highest headline.
- Gating: batch — picker shows all 3 (scored) + best together after generation.
- Selection replaces video **and** the graph segment + scores; Reject all supported.
