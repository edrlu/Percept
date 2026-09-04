# Palmier Pro — effects, layouts and keyframes

The exact vocabulary of `apply_effect`, `apply_layout` and `set_keyframes`, extracted from
`EffectRegistry.swift`, `VideoLayout.swift` and `ToolDefinitions.swift` (Palmier Pro v0.7.6).

**Anything not on these lists does not exist.** Palmier will reject an unknown effect id or
layout name, and an invented keyframe property silently wastes a call.

## 1. There is no transition tool

Palmier has no cross-dissolve, no whip-pan preset, no transition library and no plugin browser.
Every transition in a montage is **constructed** from four primitives:

| You want | Build it from |
|---|---|
| Dip to white / flash cut | `import_media` `source.matte` (hex `#FFFFFF`) as a 2–4 frame clip on the track above, or `opacity` keyframes |
| Cross dissolve | `set_clip_properties` `fadeOutFrames` on the outgoing clip + `fadeInFrames` on the incoming, overlapped on stacked tracks |
| Whip pan | `apply_effect` `blur.motion` (`radius` 40–80, `angle` matching the pan) + `set_keyframes` `position` ramp over 3–5 frames each side of the cut |
| Zoom punch | `set_keyframes` `scale` with a `hold` step on the beat frame |
| Speed ramp | `split_clips` into segments, then `set_clip_properties` `speed` per segment |
| Blur transition | `set_keyframes` `blur` up on the outgoing, down on the incoming |
| Glitch / strobe | alternating `opacity` keyframes with `hold` interpolation |

## 2. `apply_effect` — the complete effect registry

21 effects. Params are clamped to the listed range; omitted params take the default.

| Effect id | Name | Category | Params (range, default) |
|---|---|---|---|
| `color.exposure` | Exposure | Color | `ev` -3–3 (0) |
| `color.contrast` | Contrast | Color | `amount` 0.5–1.5 (1) |
| `color.saturation` | Saturation | Color | `amount` 0–2 (1) |
| `color.temperature` | Temperature & Tint | Color | `temperature` 2000–11000 K (6500); `tint` -100–100 (0) |
| `color.highlightsShadows` | Highlights & Shadows | Color | `highlights` -1–1 (0); `shadows` -1–1 (0) |
| `color.blacksWhites` | Levels | Color | `blacks` -1–1 (0); `whites` -1–1 (0) |
| `color.vibrance` | Vibrance | Color | `amount` -1–1 (0) |
| `color.wheels` | Color Wheels | Color | `lift_x` / `lift_y` -1–1 (0); `lift_m` -0.5–0.5 (0); `gamma_x` / `gamma_y` -1–1 (0); `gamma_m` 0.5–2 (1); `gain_x` / `gain_y` -1–1 (0); `gain_m` 0.5–1.5 (1) |
| `color.hueCurves` | Hue Curves | Color | curve data |
| `color.curves` | Curves | Color | curve data |
| `color.lut` | LUT | Color | `intensity` 0–1 (1) + a `.cube` resource |
| `blur.gaussian` | Gaussian Blur | Blur & Sharpen | `radius` 0–100 px (8) |
| `blur.sharpen` | Sharpen | Blur & Sharpen | `amount` 0–2 (0.4) |
| `blur.noiseReduction` | Noise Reduction | Blur & Sharpen | `amount` 0–1 (0) |
| `blur.motion` | Motion Blur | Blur & Sharpen | `radius` 0–100 px (0); `angle` -180–180° (0) |
| `stylize.invert` | Invert | Stylize | — |
| `stylize.grain` | Film Grain | Stylize | `amount` 0–1 (0); `size` 0.5–4 (1.5) |
| `stylize.vignette` | Vignette | Stylize | `amount` -1–1 (0); `midpoint` 0–1 (0.5); `roundness` -1–1 (0); `feather` 0–1 (0.5) |
| `stylize.glow` | Glow | Stylize | `intensity` 0–1 (0); `radius` 0–100 px (20); `threshold` 0–1 (0.6); `warmth` 0–1 (0) |
| `detail.clarity` | Clarity & Haze | Detail | `clarity` -1–1 (0); `dehaze` -1–1 (0) |
| `key.chroma` | Chroma Key | Key | `keyHue` 0–1 (0.333); `tolerance` 0–1 (0); `softness` 0–1 (0.1); `spill` 0–1 (0.5) |

`audio.denoise` exists as a clip effect type but is written by the dedicated `denoise_audio`
tool, not by `apply_effect`.

**Colour work belongs to `apply_color`, not `apply_effect`** — and this is enforced, not advisory:
`apply_effect` **hard-rejects any id beginning `color.`** before applying anything, with
*"'color.exposure' is a color grade — use apply_color, not apply_effect."* The `color.*` rows above
are reachable only through `apply_color`, which merges knob-by-knob, returns a reusable `color`
object, and is what `inspect_color` iterates against. `apply_effect` covers the non-colour looks:
`blur.*`, `stylize.*`, `detail.clarity`, `key.chroma`.

`apply_effect` also refuses text clips and `mediaType: 'sequence'` clips — it needs a video or
image clip.

## 3. `apply_layout` — the complete layout list

Every slot of the chosen layout must be filled. Slot names are exact.

| Layout | Slots |
|---|---|
| `full` | `main` |
| `side_by_side` | `left`, `right` |
| `top_bottom` | `top`, `bottom` |
| `pip_bottom_right` / `pip_bottom_left` / `pip_top_right` / `pip_top_left` | `main`, `inset` |
| `grid_2x2` | `r1c1`, `r1c2`, `r2c1`, `r2c2` |
| `grid_3x3` | `r1c1` … `r3c3` |
| `grid_4x4` | `r1c1` … `r4c4` |
| `main_sidebar` | `main` (70%), `sidebar` (30%) |
| `three_up` | `left`, `center`, `right` |
| `three_stack` | `top`, `middle`, `bottom` |

Grid cells count from the **top-left**: `r1c1` is top-left, `r3c3` is a 3×3's bottom-right.
PIP inset is 28% of the frame with a 3.5% margin, placed on top automatically.

Slots crop to **cover** by default (fills edge-to-edge, no stretch). `fit='fit'` letterboxes
instead. Bias the crop with `anchor` ('top', 'left', …) or continuous `anchorX` / `anchorY`
(0–1) — use these when a centred crop cuts a face off. Re-call `apply_layout` with adjusted
anchors to nudge framing; in `clipIds` mode it re-crops in place without touching timing.

Two modes, never mixed across slots: `mediaRef` per slot places **new** clips (creates one
stacked video track per slot, needs top-level `startFrame` / `endFrame`); `clipIds` per slot
re-frames **existing** clips.

**`apply_layout` clears keyframes.** On every clip it touches, in *both* modes, it sets the
position, scale, rotation and crop keyframe tracks to nil. Opacity and volume tracks survive. So
`apply_layout` always comes **before** `set_keyframes` — and re-running a layout later to nudge
`anchorX` destroys any motion rig on those clips, which must then be re-authored.

## 4. `set_keyframes` — property value layouts

One property, one clip, per call. Replaces that property's whole keyframe track (pass `[]` to
clear). **Frames are CLIP-RELATIVE** — 0 is the clip's first frame — so keyframes travel with
the clip when it moves. Rows are `[frame, ...values, interp?]` with `interp` ∈ `linear`,
`hold`, `smooth` (default `smooth`). Last row wins on a duplicate frame.

| Property | Row layout | Notes |
|---|---|---|
| `opacity` | `[frame, value]` | 0.0–1.0 |
| `volumeDb` | `[frame, decibels]` | −60 to +15; −60 is mute |
| `rotation` | `[frame, degrees]` | clockwise |
| `position` | `[frame, topLeftX, topLeftY]` | **TOP-LEFT corner**, 0–1 canvas coords, *not* the centre. A full-canvas clip's top-left is (0, 0); a centred half-size clip's is (0.25, 0.25). |
| `scale` | `[frame, width, height]` | normalized **size** in 0–1 canvas coords (1.0 fills that axis), *not* a multiplier. Match the w/h ratio for uniform scaling. |
| `crop` | `[frame, top, right, bottom, left]` | 0–1 insets of the source. Constant crop belongs on `set_clip_properties`. |
| `blur` | `[frame, radius]` | whole-layer Gaussian, 0–100 px. Works on video, images, text and nested timelines. |

Motion keyframes (`position` / `scale` / `rotation`) override the clip's static `transform`
while active. Setting `volumeDb`, `opacity`, `transform.rotation` or `crop` via
`set_clip_properties` **clears** that property's keyframe track.

Never build split-screen, PIP or grid layouts out of `position` / `scale` / `crop` keyframes —
that is `apply_layout`'s job, and hand-built layouts drift out of alignment.

## 5. `set_clip_properties` — the static counterparts

`speed` (1.0 normal, <1 slower and longer, >1 faster and shorter), `volumeDb` (−60…+15),
`opacity` (0–1), `fadeInFrames` / `fadeOutFrames` (clip-relative lengths, 0 clears, sum must
fit the clip), `edgeRounding` / `edgeSoftness` (0–1), `durationFrames`, `trimStartFrame` /
`trimEndFrame` (offsets into the **source**), `transform`, `crop`, `blendMode`.

Fades **multiply** existing opacity/volume keyframes rather than replacing them. Fades are
per-clip and do **not** propagate to a linked partner — pass both the video clip id and its
nested `audio.id` to fade picture and sound together. Timing changes (trims, `durationFrames`,
`speed`) *do* propagate to the linked partner so sync is preserved.
