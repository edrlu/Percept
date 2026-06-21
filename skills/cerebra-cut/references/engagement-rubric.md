# Engagement dimensions & how to read them

The Cerebra MCP returns four engagement **dimensions**, each a manually defined cortical
surface proxy on the fsaverage5 mesh that TRIBE v2 predicts over. They are display proxies,
not anatomical ground truth and not psychological measurements.

| key                  | label / tag              | reads as (loose, for copy only)                 |
|----------------------|--------------------------|-------------------------------------------------|
| `reward_desire`      | Ventromedial PFC (vmPFC) | anticipation / "I want this" pull                |
| `emotional_response` | Anterior temporal (aTEMP)| affective charge / emotional salience            |
| `personal_relevance` | Lateral PFC (lPFC)       | "this is about me / matters to me" relevance     |
| `memory_encoding`    | Ventral temporal (vTEMP) | stickiness / likely-to-be-remembered             |

## Interpreting the response

- `global` — per-frame overall engagement, 0–100. The shape matters more than absolute
  values; look for the spikes.
- `regions` — the four dimensions, sorted strongest-first, each with a `score` (its peak)
  and per-frame `values`. The top region tells you *why* a moment lands (e.g. emotional vs.
  reward-driven).
- `peaks` — already-ranked, non-overlapping `[start_s, end_s]` windows around each spike,
  tagged with the dominant `dimension` at that moment. This is what you cut to.
- `peak` — the single strongest frame.

## Honesty rules for anything you show the user

- Say **"predicted engagement"** / **"TRIBE v2 predicts…"**, never "measures" or "detects."
- These are **population-average** proxies — not this viewer's reaction.
- The four dimensions are **labels for cortical regions**, not verified emotions. Use the
  "reads as" column only as soft copy, and only when it helps the user choose a cut.
- Cerebra is a research/visualization signal, **not** an fMRI scanner, diagnostic, or
  behavioral truth machine.
