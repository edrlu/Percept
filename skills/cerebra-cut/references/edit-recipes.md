# Edit recipes — turning peaks into a cut

All recipes consume `peaks` from `predict_engagement` and render with the Pika edit tools.
Trim ranges are `[start_s, end_s]` in seconds; clamp every range to `[0, duration]`.

## Recipe A — Single highlight (default)

Best when the user wants "the best part" or one short clip.

1. Take `peaks[0]` (`rank: 1`).
2. If the user gave a target length `T`, recenter on `center_s`: `start = center_s - T/2`,
   `end = center_s + T/2` (clamped). Otherwise use the peak's own `start_s`/`end_s`.
3. `edit_trim` that range → `add_captions` → deliver.

## Recipe B — Top-N reel

Best for "top 3 moments", a montage, or a longer source (talks, vlogs).

1. Choose N (user-specified, else 3). Take `peaks[0..N-1]`.
2. **Sort the chosen peaks by `start_s` ascending** (chronological), not by score — the reel
   should play in time order.
3. `edit_trim` each range → `edit_concat` in that chronological order.
4. `add_captions` on the concatenated result → deliver.
5. Mention each segment's time + dominant `dimension` in the summary.

## Recipe C — Tighten (keep most, drop the dead air)

Best for "make it tighter" / "trim the boring parts" without reordering.

1. From `global`, find contiguous stretches below ~the 35th percentile of engagement.
2. Keep the complementary high-engagement spans; build trim ranges for those.
3. `edit_trim` each kept span → `edit_concat` in order → `add_captions` → deliver.
4. Note how much was removed (original `duration` → final length).

## Caption style guidance

- Social / vertical (TikTok, Reels, Shorts): `tiktok` or `hormozi`.
- Talks, demos, interviews: `classic`.
- Match the source's vibe from what the user said; ask only if genuinely unclear.

## Edge cases

- **Very short video** (`duration` ≲ a few × `tr`): peaks may overlap or there may be just
  one. Prefer Recipe A; don't force N clips.
- **Flat curve** (all `global` values close): say so — there's no strong peak; offer Recipe
  A on `peak` or Recipe C, and tell the user the signal is weak.
- **A range hits a boundary**: clamp, don't error. A clip that would start < 0 or end >
  `duration` just gets trimmed to the edge.
