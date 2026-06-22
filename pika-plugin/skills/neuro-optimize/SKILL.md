---
name: neuro-optimize
description: >-
  Automatically make a video MORE engaging by closing the loop: generate with
  Pika, score with Meta's TRIBE v2 brain model (neuro-eval), find the weakest
  beat, generate a new 3-second candidate, re-score, and repeat until the predicted
  engagement score stops climbing. Turns Pika from "generate a video" into
  "generate the video that wins" — a self-improving training loop with a neural
  reward signal and zero humans in the inner loop. Use when the user says
  "make this video better", "optimize my ad", "improve engagement", "auto-tune
  this video", "neuro-optimize", "raise the brain score", "fix the boring parts
  automatically", or "keep iterating until it's good". Built on the neuro-eval
  endpoint (NEURO_API_URL / NEURO_API_KEY) plus Pika `generate_video`,
  `extract_frame`, and `task_status`. Optionally validates the final winner with a
  small human panel if a rating provider is configured. Every candidate is
  normalized to at most 3 seconds and the loop is capped at 5 iterations.
---

# neuro-optimize — a self-improving video loop with a brain-model reward

## The idea
Every Pika generation today is one-shot: render, ship, hope. This skill adds the
missing **feedback loop**. It uses the TRIBE v2 engagement score (from
**neuro-eval**) as a cheap, instant **reward signal**, so the agent can iterate
on a video the way an optimizer iterates on a loss — *without* paying for a human
panel on every step.

```
 brief / seed video
        │
        ▼
 ┌──────────────────────────────────────────────┐
 │  LOOP (reward = candidateScore - bestScore)    │
 │                                               │
 │   generate_video ──► score (/score) ──► diagnose weak window │
 │        ▲                                   │   │
 │        └──── generate next candidate ◄─────┘   │
 │                                                │
 │   stop when score plateaus (Δ < ε) or budget   │
 └──────────────────────────────────────────────┘
        │ best clip + score history
        ▼
 (optional) confirm the winner with a small human panel
```

## Prerequisites
- A running TRIBE v2 endpoint — see **neuro-eval** (`NEURO_API_URL`,
  `NEURO_API_KEY`; Colab notebook in `colab/`). Verify `GET /health` is `ready`.
- Pika MCP connected (this is the Pika agent), for `generate_video`,
  `extract_frame`, `task_status`.

## Parameters (ask or infer)
| Param | Default | Meaning |
|---|---|---|
| `seed` | — | A brief (text → first generation) OR an existing video to improve. |
| `provider` | `seedance` | Pika generation model (`seedance` or `kling`). |
| `maxIters` | `5` | Hard cap; never exceed five candidates. |
| `epsilon` | `0.5` | Stop when reward is below this for 2 rounds. |
| `candidateSec` | `3` | Every candidate must be at most 3 seconds. |
| `candidatesPerRound` | `1` | Generate N variants, keep the highest-reward candidate. |

## The loop — run this end to end

### Round 0 — establish a baseline
1. If `seed` is a **brief**, call `generate_video` to produce the first 3-second
   clip. If `seed` is a **video**, use the server's normalized first 3 seconds.
   Download the result to `work/iter0.mp4`.
2. Score it (neuro-eval):
   ```bash
   curl -s -X POST "$NEURO_API_URL/score" -H "x-api-key: $NEURO_API_KEY" \
     -F "video=@work/iter0.mp4" > work/iter0.json
   ```
3. Record `adScore`, `adScoreBreakdown`, and `weakWindow` as the current best.

### Round k — fix the weakest beat
4. Read `rewardFeedback.generator_instruction`, `weakWindow`, the weakest TRIBE
   region, and OpenCV diagnostics from the latest report. This is the direction
   for the next candidate.
5. Extract the first and final boundary frames from the current best candidate:
   ```
   extract_frame(video=<best>, time=<slot start>) -> frame_start.png
   extract_frame(video=<best>, time=2.9)          -> frame_end.png
   ```
6. Write a **targeted prompt** for the next 3-second candidate:
   - low `LANG` in the window → sharpen the spoken line / on-screen message there;
   - low `VIS` → add motion, a camera move, a visual reveal;
   - low `AUD` → lift audio energy / add a beat hit / emphasis;
   - low `ATTN` → introduce a salient change (cut, contrast, surprise).
7. Regenerate a complete 3-second candidate with `generate_video`
   (image_to_video), using boundary/reference frames when continuity matters:
   - `seedance`: `image=frame_start`, `end_image=frame_end`, `fast:true`,
     `resolution:"720p"`, matching `aspect_ratio`, `duration=3`.
   - `kling`: `image=frame_start`, `image_tail=frame_end`,
     `quality_mode:"pro"`, `prompt_adherence:"strict"`, `duration=3`.
   If `candidatesPerRound > 1`, generate N variants.
8. **Re-score** the candidate with `/score`. Compute
   `reward = candidateScore - bestScore`. Keep it only when reward is positive;
   otherwise retain the prior best. Feed the returned generator instruction into
   the next prompt. The frozen TRIBE model is the critic/reward model; Pika is
   the generator being improved.

### Stop
Stop when any holds:
- reward is `< epsilon` for two consecutive rounds (plateau),
- `maxIters` reaches 5, or
- the same generator instruction fails to improve twice.

### Report
Show the **score climbing across rounds** — this is the proof the loop works:
```
NEURO-OPTIMIZE  (provider=seedance, 3 rounds)
  iter0  58.4   baseline; weak 0.0–1.0s
  iter1  64.1   reward +5.7; strengthened visual reveal
  iter2  67.9   reward +3.8; lifted audio/attention beat
  iter3  68.4   reward +0.5; plateau → stop
WINNER: work/iter3.mp4  (68.4)
```
Return the winning file, the score history, and a one-line rationale per round.

## Optional — validate the winner with humans
The neural score is a *proxy*. If a human-rating provider is configured (e.g. a
Terac MCP), run **one** small panel (n≈3) on the final winner only — never on
every inner step — to confirm the proxy's pick before the user ships it. Humans
calibrate and validate; the brain model does the fast inner-loop iteration.

## Guardrails
- **Never regress:** only accept a candidate if its reward is positive.
- **Budget aware:** each round costs Pika generation credits + ~1 GPU score call;
  respect the five-iteration cap. Use `fast`/`720p` candidates for cheap iteration, then do a final
  full-quality render of the winning prompt.
- **Be honest:** the reward is predicted population-average cortical engagement,
  not measured sales. Present the climb as a directional optimization, and lean on
  the optional human panel for the final call.
