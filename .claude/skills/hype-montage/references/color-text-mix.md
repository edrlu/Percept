# Colour, text and the mix

The finishing phases, by the numbers. Each runs only after the cut is locked.

- [1. Colour](#1-colour)
- [2. Text](#2-text)
- [3. The mix](#3-the-mix)

---

# 1. Colour

Grade after the cut is locked and the motion pass is authored. Patch clip ids from each mutation's
delta; re-read `get_timeline` only after `undo`, `create_timeline(from:)` or `set_active_timeline`
(every id in a copy is new), or a failure suggesting staleness.

## Route

Grading is `apply_color`, exclusively. **`apply_effect` hard-rejects every `color.*` id** —
*"'color.exposure' is a color grade — use apply_color, not apply_effect."* Use `apply_effect` only
for `stylize.glow`, `stylize.grain`, `stylize.vignette`, `detail.clarity`, `blur.sharpen`.

`apply_color` **merges** — pass only the knobs you are nudging. `reset: true` starts from neutral.
`color:` replaces the whole grade and is mutually exclusive with `reset` and the individual knobs.

## Hero clip first

Pick the most representative daylight clip — good skin, wide tonal range — and grade only that one.

```json
{"clipIds":["clip_a4"],"exposure":0.15,"contrast":1.10,"temperature":7000,
 "tint":-4,"highlights":-0.25,"shadows":0.10,"blacks":-0.08,"whites":0.05,
 "vibrance":0.28,"saturation":1.0,
 "shadowsHue":205,"shadowsAmount":0.12,"highsHue":38,"highsAmount":0.09,"highsGain":1.04,
 "masterCurve":[[0,0.025],[0.25,0.23],[0.72,0.77],[1,0.95]],
 "hueCurves":{"targets":[{"targetHue":30,"satScale":1.05,"lumShift":0.02},
                         {"targetHue":210,"satScale":1.15,"hueShift":-6},
                         {"targetHue":120,"satScale":0.85}]}}
```

`temperature` is inverted from a Kelvin colour scale: **higher = warmer**, 6500 neutral. Positive
`tint` is green. The `[1, 0.95]` curve endpoint is the highlight roll-off. `hueCurves.targets`
replace rather than merge, and are selective to about ±22°, so 210 catches sky without touching the
30° skin cluster.

## Grade by the numbers

Loop `apply_color` → `inspect_color(clipId)` → adjust. Read `luma`, `balance`, `saturation` and
`hueHistogram12` — a flat 12-element array, each bin 30° from red, so `[1]` is 30–60°
(skin/orange) and `[7]` is 210–240° (sky). **Never eyeball the returned frame.**

Hero targets: black point 0.02–0.05 (never 0.000), white point 0.92–0.96, `clipLowPct` and
`clipHighPct` both under 1.0%, `balance.warmCool` +0.03…+0.07, `greenMagenta` −0.01…+0.01.

## Shot-matching across phones

`inspect_color.reference` takes a **media asset id, not a clipId**. Build references with
`capture_frame(timelineFrame: <hero midpoint>)` — but that mode bakes the **full composite**
(every video track plus text and captions), so pick a frame where the hero is the only visible
layer. Confirm with `inspect_timeline({startFrame: <candidate>})`, whose metadata lists visible clip
ids top-down. If overlays cover the hero everywhere, capture from its source instead
(`capture_frame({mediaRef, sourceSeconds})`) and accept an ungraded reference — or capture before
the text phase.

Capture one reference **per lighting bucket**, three maximum (daylight, golden hour, night). Never
one global reference, or you drag a 3200 K bar shot to 6500 K and kill it.

Per outlier, `inspect_color({clipId, reference})` and converge one domain per call, re-measuring
after each:

1. `exposure` ≈ `log2(refLumaMean / currentLumaMean)`, clamped to ±0.6 EV, then `blacks` / `whites`.
2. `temperature` — nudge ~200 K per 0.03 of `gap.warmCool`, re-measure, repeat. The bias is
   uncalibrated, so converge on it rather than converting it. Then `tint`.
3. `contrast`, `highlights`, `shadows`.
4. `vibrance`, then `saturation`.

`gap.hints` covers only blacks, warmCool, greenMagenta and saturation, and only past
0.03 / 0.03 / 0.02 / 0.03 — there is no exposure hint. **Stop when `hints` is empty and
`gap.lumaMean` and `gap.lumaWhite` are both within ±0.03.** Budget three iterations per clip.

Action-cam beside iPhone: `saturation` 0.88, `contrast` 0.92. Night phone: `blacks` −0.15,
`shadows` +0.10.

For auto-exposure drift inside one shot, call `inspect_color({clipId, atFrame: ...})` at 25% and
75% of the clip — `atFrame` is a **project** frame, not clip-relative. If `luma.mean` differs by
more than 0.08, `split_clips` at the drift and grade each half. **There are no colour keyframes.**

## Propagate

Read the hero's `color` object and fan it out in one call:

```json
{"clipIds":["clip_b1","clip_b2","..."],"color":{ /* hero color object */ }}
```

This replaces only the grade and leaves `apply_layout` framing, punch-in transforms and keyframes
alone.

**Do not use `copy_clip_settings` to move a grade.** It copies the whole static visual setup —
transform, crop, opacity, edge rounding and softness, blend mode and the *entire* effect stack —
and settings absent from the source **clear** the target's. It will wipe each target's layout
framing and overwrite its non-colour effects. Keyframe tracks survive but will now animate off a
transform from the wrong clip.

Do not pass `color:` to a clip you just hand-tuned. After the fan-out, match outliers with small
merge calls (`temperature: 6600` on golden hour; `temperature: 7600, shadows: 0.18` in open shade).

## Glue pass

Identical values on every visual clip, one call. Matching grain is what actually sells "one camera":

```json
{"clipIds":["..."],"effects":[
 {"type":"stylize.grain","params":{"amount":0.10,"size":1.5}},
 {"type":"stylize.vignette","params":{"amount":-0.18,"midpoint":0.58,"feather":0.7,"roundness":0.2}},
 {"type":"stylize.glow","params":{"intensity":0.15,"radius":32,"threshold":0.70,"warmth":0.5}},
 {"type":"detail.clarity","params":{"clarity":0.18,"dehaze":0.15}}]}
```

On a clip with no such effect, omitted params take defaults — all no-ops. On a clip that already
carries it, omitted params keep their **current** value; to neutralise one, pass the no-op
explicitly or list its type in `remove`. Out-of-range values are clamped silently.

Add `blur.sharpen` (`amount` 0.3–0.5) only to soft clips. Never touch `blur.gaussian.radius` on a
clip carrying a keyframed `blur` punch — setting it clears those keyframes.

## Guardrails

| Knob | Working range | Hard stop |
|---|---|---|
| `exposure` | ±0.25 EV for matching | ±0.75 |
| `contrast` | 1.06–1.12 | 1.20 |
| `blacks` / `whites` | −0.10…−0.05 / 0…+0.06 | −0.15 / +0.12 |
| `vibrance` / `saturation` | +0.15…+0.28 / 1.00 | +0.40 / 1.15 |
| `temperature` / `tint` | 6600–7100 K / −6…+4 | 7400 K / ±12 |
| `highlights` / `shadows` | −0.15…−0.30 / +0.05…+0.12 | −0.45 / +0.20 |
| `shadowsAmount` / `highsAmount` | 0.06–0.12 / 0.04–0.10 | 0.18 / 0.15 |
| `stylize.vignette.amount` | −0.12…−0.22 (negative darkens) | −0.30 |
| `stylize.grain.amount` | 0.06–0.12 | 0.18 |
| `lut.strength` | 0.35–0.60, always explicit | default is 1 |

- **Do not** reach for `saturation` for global pop — `vibrance` protects skin; `saturation` 1.35
  turns faces into traffic cones. Verify: `hueHistogram12[1]` must not exceed the ungraded reading
  by more than ~20%.
- **Do not** buy contrast with `contrast` 1.35 + `blacks` −0.4 on 8-bit phone footage. Take it from
  the `masterCurve` toe and shoulder with `contrast` near 1.0.
- **Do not** push sky with `saturation`; tame it — `blueCurve: [[0,0],[0.7,0.7],[1,0.88]]` plus a
  `hueCurves` target at 210 with `satScale` ≤ 1.20.
- **Do not** grade 40 clips independently. At two cuts per second the look strobes.
- **Do not** fabricate a `.cube` path. With no real LUT file, build the look from `masterCurve` and
  the wheels.

## No adjustment layer

Palmier has no masks, power windows, adjustment layers or colour keyframes. `apply_color`,
`inspect_color` and `apply_effect` all **refuse** clips with `mediaType: 'sequence'`, so you cannot
nest the montage and grade the carrier. Hue-qualified `hueCurves` targets and `stylize.vignette` are
the only spatial isolation you get.

One global look = grade the hero, then fan its `color` object out to every video and image clip in
one call.

A grade that ramps into a drop: `split_clips` on the drop frame and grade the downstream half — or
re-place the same media on a track above with the same `source` seconds, grade that copy hot, and
`set_keyframes` its `opacity` `[[0,0],[10,1]]` (clip-relative to the copy's own start).

Close the phase with `inspect_timeline({startFrame: 0, endFrame: <total>, maxFrames: 12})` and check
continuity across the strip in one read.

---

# 2. Text

Text lands after the cut, after motion, after colour. It is the last thing added and the first
thing cut.

## Which tool

`add_texts` for authored overlays you write: the opener, location cards, an end card.
`add_captions` for spoken audio only — point it at a dialogue track, never at the music bed (it
throws *"No speech detected"*). A montage cut to music usually needs **zero captions**; add them
only if a piece of sync dialogue carries the joke. If you do caption, omit `style` / `animation` /
`transform` at creation, then restyle the whole group in one `update_text` with the returned
`captionGroupId`.

Never `generate_image` a title card — Palmier's own instructions forbid it, and it costs money.

## What the cards say

Place names, not sentences: `"SAN DIEGO\nMARCH 2026"`, `"SUNSET CLIFFS"`. Caps: ≤ 3 words per line,
≤ 2 lines, ≤ 18 characters per line, the date exactly once on the opener. Budget **one card per
25–30 s, four maximum**. Card a location only when it changes *and* the shot does not announce
itself. Two lines go in one `content` with `\n`, never two clips.

## Frame maths

Cards land on **downbeats**, and the entrance must *finish* on the downbeat:
`startFrame = timelineFrame(D) − E`, where E is the entrance length (4–6 frames at 30 fps).
`endFrame` is the next bar's downbeat. Readability floor: `0.6 s + 0.25 s per word`.

Batch **every card into one `add_texts` call with `trackIndex` omitted on all entries** — that
creates one new top video track. Mixing set and omitted `trackIndex` across entries throws, and
separate calls stack up junk title tracks. The new track lands at index 0, so shift every track
index you already know by +1 yourself, from the `createdTracks` delta.

```json
{"entries":[
 {"startFrame":76,"endFrame":160,"content":"SAN DIEGO\nMARCH 2026",
  "transform":{"x":0.5,"y":0.5},
  "style":{"fontSize":110,"fontCase":"uppercase","alignment":"center","tracking":10,
           "lineSpacing":8,"color":"#FFFFFF",
           "shadow":{"enabled":true,"color":"#000000","opacity":0.75,
                     "offset":{"x":0,"y":0},"blur":20}}},
 {"startFrame":1276,"endFrame":1360,"content":"SUNSET CLIFFS",
  "transform":{"x":0.08,"y":0.82},
  "style":{"alignment":"left","fontSize":64,"fontCase":"uppercase","tracking":8}}]}
```

## Kinetic type

Text clips take `set_keyframes` on `opacity`, `position`, `scale`, `rotation` and `blur`. `crop` and
`volumeDb` do nothing on text — they are accepted without error and write a track that never
renders. `apply_effect` refuses text clips outright; for glow-adjacent softness use `style.blur` or
the `blur` track.

**The scale trap.** For text, `scale` rows are `[frame, width, height]` in normalized canvas units,
and the identity value is the clip's own auto-fit box — which is **not readable**: `get_timeline`
and every delta strip `transform.width`/`height` off text clips. So default to `opacity` + `blur`
(+ `position`), which need no size read. If a slam is required, measure first: `inspect_timeline
{startFrame}` at the settle frame burns a 0–1 grid — read W and H off it, write the settle row at
exactly that pair, and scale earlier rows from it. Writing `[0,0.6,0.6],[5,1,1]` blind blows the
type to full canvas.

Slam at 30 fps, clip-relative, with W = 1.00 and H = 0.57 measured off the grid:

```json
{"clipId":"txt_1","property":"opacity","keyframes":[[0,0,"linear"],[2,1,"hold"]]}
{"clipId":"txt_1","property":"blur",   "keyframes":[[0,20,"smooth"],[4,0,"hold"]]}
{"clipId":"txt_1","property":"scale",  "keyframes":[[0,0.55,0.31,"smooth"],[4,1.06,0.60,"smooth"],[7,1.00,0.57,"hold"]]}
```

Text scale is uniform — the renderer takes `min(widthRatio, heightRatio)`, so a mismatched pair
animates to the smaller value rather than stretching. To actually stretch glyphs use
`style.widthScale` / `style.heightScale`. `smooth` is plain smoothstep with no overshoot, so a
4–7 frame over-and-settle at 1.04–1.10× is the only way to get a snap.

Working numbers: entrance 4–6 frames, blur-in 16–28, opacity ramp 2–3 frames, slide-in 0.04–0.08
canvas units over 5–6 frames with `hold` on the settle row. Exit: hard cut at `endFrame`, or
`fadeOutFrames: 4`. Anything ≥ 12 frames reads sleepy.

If you add a `position` track, auto-centering stops — recompute top-left per row as
`centerX − w_row/2`, `centerY − h_row/2`.

Presets are the cheap path when 6 frames is enough. `animation` accepts exactly `off`, `popIn`,
`slideUp`, `typewriter`, `wordReveal`, `wordSlide`, `highlightPop`, `highlightBlock`. `popIn` is
hard-coded to 6 frames. Presets render inside the text layer and **multiply** with your keyframes —
pick one path per clip.

## Style and safe areas

`fontSize` is in 1080-**height** reference points, so it is a constant fraction of height and a
varying fraction of width. At 1920×1080: 96–120 for the opener, 54–72 for location cards,
`tracking` 6–14, `lineSpacing` 8 on two-line cards, `fontCase: "uppercase"`.

`transform.x` is the alignment-relative anchor (left edge, centre or right edge); `transform.y` is
the vertical **centre** — a different space from `set_keyframes position`, which is the top-left
corner.

Legibility over moving footage, in order:

1. Halo shadow `{enabled:true, color:"#000000", opacity:0.75, offset:{x:0,y:0}, blur:20}` — best
   all-rounder, holds on sand and in dark interiors.
2. `outline {enabled:true, color:"#000000", width:5}` — below 3 it vanishes, above 8 it reads as a
   sticker.
3. `background {enabled:true, color:"#000000", opacity:0.45, padding:{x:26,y:10}, cornerRadius:14}`
   — the hard hip-hop plate.

Any title spanning a cut gets outline or plate; shadow alone dies on one sun-glare frame.

Text auto-wraps at 90% of canvas width and there is no parameter to change it — control breaks with
`\n`.

**For the 9:16 version:** aspect changes re-fit the text box, but `fontSize` is pinned to the
1080-height reference, so the same value covers (16/9)² = 3.16× more width. After
`set_project_settings`, `update_text` each authored clip with `style.fontSize × 0.32` (floor 12) and
re-broken lines, one clipId at a time. For caption groups send **style only** —
`update_text {captionGroupId, style:{...}}`; never pass `content` with a `captionGroupId`, it
rewrites every clip in the group to that one string and destroys the transcript.

Vertical legal box: x ∈ [0.06, 0.80], y ∈ [0.12, 0.78]; titles at y 0.26–0.38, captions at y 0.74.
`add_captions` defaults to a lower third that sits under the platform UI — always follow with
`update_text {captionGroupId, transform:{y:0.74}}`.

Verify placement with `inspect_timeline {startFrame}` at the **worst** frame — the brightest shot
under the title, not the first.

## Do not

- Do not use per-word presets (`wordReveal`, `wordSlide`, `highlightPop`, `highlightBlock`) on a
  title card. Authored text carries no word timings, so they spread words evenly across the whole
  clip: word *i* starts at `duration × i / wordCount`. On an 84-frame, 4-word card, word three
  starts at frame 42 — half a bar late. Those presets are for captions.
- Do not write `scale` rows off guessed numbers.
- Do not mix `fadeInFrames` with an `opacity` keyframe track — fades multiply the track.
- Do not set `update_text transform.rotation` on a clip you keyframed rotation onto, or
  `style.blur` on one with a blur track: each clears the matching keyframe track. Order every clip
  `add_texts` → `update_text` (style) → `set_keyframes` last.
- Do not overlap two titles on one track expecting an error — `add_texts` clears the region first
  and the later clip silently eats the earlier one. Two simultaneous titles need a second
  `add_texts` call with `trackIndex` omitted, creating another top track.
- Do not start the entrance on the beat. Subtract E.

---

# 3. The mix

Palmier has **no meter, no LUFS readout, no limiter, no compressor, no EQ and no sidechain**, and
you cannot hear the timeline. So mix by fixed numbers, verify structure with `get_timeline`, and
tell the user to listen once and move one number.

## Order of operations — never deviate

1. `denoise_audio` (it changes perceived level).
2. `set_clip_properties` — static `volumeDb` and fades.
3. `set_keyframes` — ducks, last.

`set_clip_properties volumeDb` **clears the keyframe track on that property**. Once a clip is
ducked, never touch its `volumeDb` that way again. Fades *multiply* volume keyframes, so never let
a fade region overlap a duck ramp or its hold — overlapping a region anchored at 0 dB is fine, and
is exactly how the ending fade works.

## Address the audio, not the picture

`volumeDb` and fades do **not** propagate across a link. From `get_timeline` collect two id sets —
the nested `audio.id` of every video clip, plus the top-level ids of everything on an audio track —
and pass both. Passing the video id mutes nothing and fades the picture instead: the single most
common failure in this phase.

## Levels (30 fps; scale frame counts with your fps)

Mute everything first, then reopen deliberately. An absent `volumeDb` in `get_timeline` means
**0 dB, not silence** — untouched nat sound ships as wind and hiss under every shot.

Three calls, not one: `fadeInFrames + fadeOutFrames` must fit each clip's duration, so one short
clip in a 2+2 batch rejects the whole call.

```json
{"clipIds":["<every audio id, both sets>"],"volumeDb":-60}
{"clipIds":["<those >= 5 frames>"],"fadeInFrames":2,"fadeOutFrames":2}
{"clipIds":["<those 2-4 frames>"],"fadeInFrames":1,"fadeOutFrames":1}
```

- **Music bed: `volumeDb: 0`.** This is the only place the bed's level is committed. Never above
  `+3` — without a limiter, clipping is baked into the render. If it feels quiet, pull nat sound
  down instead.
- Texture (waves, crowd, car interior): `-18`.
- Featured pop: `0` to `+4` non-verbal, `0` to `+2` for a clear line.
- Anything under 24 frames that is not a featured pop, any clip with `speed ≠ 1.0`, any pure
  cut-to-beat run: `-60`. Set featured pops **last**, after the blanket pass, so the `-60` rule
  cannot claw them back.
- Three nat-sound levels only: `0`/+ for a featured pop, `-18` for texture, `-60` for off. If you
  are typing `-25`, pick `-18` or `-60`. Intermediate values are indecision, not a mix.

**Pop budget:** 3–5 per 60–90 s, at least 90 frames apart. Non-verbal 8–24 frames; a dialogue quip
30–45 frames, never a full sentence. `denoise_audio` only on pops you keep (`strength: 0.5`, never
above 0.85, never on a `-60` clip).

## Ducking — one call per music clip

`set_keyframes` replaces the whole track, so batch **every** duck into one call, rows sorted, frames
**clip-relative** (`rel = timelineFrame − musicStartFrame`).

Anchor row 0 at 0 dB so the opening does not interpolate up into the first duck, and anchor the
clip's last frame (`durationFrames − 1`) at 0 dB so the tail is defined before the ending fade
multiplies it. Down in 6 frames `linear`, up in 14 `smooth`, landing the recovery on a downbeat. A
row's interp governs the segment **leaving** that keyframe, so `smooth` sits on the `relQ` row.

```json
{"clipId":"<musicAudioId>","property":"volumeDb",
 "keyframes":[[0,0,"linear"],[relP-6,0,"linear"],[relP,-10,"linear"],
              [relQ,-10,"smooth"],[relQ+14,0,"linear"],[lastFrame,0,"linear"]]}
```

Depth: `-10` under a word you must understand, `-5` under a scream or splash (fully ducking a
non-verbal pop drains it — it should fight the music), `-6` under a title card.

**Do not** leave `manage_tracks set:[{muted:true}]` as a final state; it is an audition tool.
Commit with per-clip `-60`.

## Overhangs (J and L cuts)

Hand-built, since there is no transition tool. `manage_clip_links action:'unlink'`, then on the
audio clip alone: extend the tail 6–12 frames past the picture cut with `durationFrames` (or a
smaller `trimEndFrame`), or start it 4–8 frames early with `move_clips toFrame` plus the same count
knocked off `trimStartFrame` and added to `durationFrames`. Then `manage_clip_links action:'link'`
with both ids.

Shape a pop on its `audio.id`: `fadeInFrames: 3, fadeOutFrames: 8` on pops ≥ 12 frames; `2` in and
`4` out on an 8–11 frame pop. If it starts mid-clip, keyframe in over at least 3 frames — a `hold`
jump from `-60` to `+4` is a click.

## Fades on every boundary

Every audio boundary gets ~70 ms in and out: 2 frames at 30 fps, 2 at 24, 4 at 60. On flash cuts
under ~8 frames use 1+1. `remove_silence`, `split_clips` and `ripple_delete_ranges` mint new
boundaries with **zero** fades — take the clips from the mutation result and immediately re-run the
2-frame pass.

## Ending cleanly

A bar is `(60/bpm) * 4 * fps` frames. Land the last picture cut on a downbeat, let the music run one
full bar past it, then `fadeOutFrames: 30` on the music and `fadeOutFrames: 18` on the last picture
clip — each fade ends at its own clip's last frame. If the last shot is too short to cover the bar,
stretch it with `speed: 0.4` rather than cutting the music mid-phrase.

A hard stop is correct only when the track ends within a beat of its last downbeat — compare
`detect_beats`' last downbeat against the asset's `durationSeconds`.

Report the mix as offsets, not as a judgment: *"bed 0, nat sound −60, texture −18 on three shots,
ducked to −10 under the two spoken lines."* Never claim it is balanced.
