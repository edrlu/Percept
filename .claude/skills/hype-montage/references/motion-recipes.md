# Motion recipes

Palmier has **no transition tool**. Everything here is constructed. Read
`effects-layouts-keyframes.md` for the valid effect ids and keyframe row layouts first.

## The four laws these recipes assume

1. **Order is fixed per clip:** `split_clips` → `set_clip_properties` → `apply_layout` →
   `set_keyframes`. `apply_layout` **clears** a clip's position, scale, rotation and crop keyframe
   tracks (opacity and volume survive), so re-running a layout to nudge `anchorX` destroys the rig.
2. **Read the base extent before writing any scale or position row.** `get_timeline` omits only an
   *identity* transform, so a clip whose source aspect differs from the canvas reports
   `transform.width`/`height`. Call those `(w0, h0)`, defaulting to `(1, 1)` when absent. Every
   scale row is `[frame, w0*factor, h0*factor]`; the clip's resting top-left is
   `((1-w0)/2, (1-h0)/2)`, **not** `(0, 0)`.
3. **Scale alone stays centred.** With no active position track, the clip is centred on
   `transform.center` as it scales. Write a position track only for an *off-centre* push, or to
   overwrite a stale one — an active position track beats the static centre, so a leftover track
   silently wins.
4. **Frames are clip-relative.** 0 is the clip's first frame. Keyframes travel with the clip.

Verify every accent with
`inspect_timeline {startFrame: beatFrame-1, endFrame: beatFrame+3, maxFrames: 4}`. The burned-in
0–1 grid shows drift, black edges and stacking order.

## Punch-in (the workhorse)

Use `hold` on the pre-roll row — the default `smooth` eases the value up *before* the beat and the
hit lands mushy. For a centred punch on an identity clip, the scale track is all you need:

```json
{"clipId":"clp_a","property":"scale","keyframes":[[k-1,1.00,1.00,"hold"],[k,1.10,1.10,"hold"]]}
```

Add a paired `position` track only to push off-centre (toward a face, say), or to clear a stale
one. Off-centre target `(cx, cy)` at factor `f`: `[k, cx - w0*f/2, cy - h0*f/2, "hold"]`.

4–6% is felt not seen; **1.10 is the default hit**; 15–20% only on locked-off shots or faces;
past 25% is cheap. Recoil variant:
`[[k,1.12,1.12,"hold"],[k+3,1.05,1.05,"linear"],[k+7,1.03,1.03,"linear"],[k+12,1.02,1.02,"smooth"]]`.
Punch **out** (1.12 `hold` → 1.00 on the beat) on every third or fourth hit so it does not ratchet.

A 1080p source on a 1080p canvas spends real resolution past 1.0 — cap at 1.10 and pay it back with
`apply_effect blur.sharpen` `amount` 0.20–0.30 (never above 0.5) or `detail.clarity` `clarity`
0.10–0.18, on that clip only.

## Whip pan

`blur.motion` params are static — they cannot ramp — so carve the smear out instead of keyframing
it. At cut frame C, 30 fps, split a 3-frame sliver each side:

```json
{"trackIndex":1,"frames":[C-3,C+3]}
```

Then `apply_effect [{"type":"blur.motion","params":{"radius":65,"angle":0}}]` on **the two slivers
only**. `angle` 0 is horizontal, 90 vertical — match the travel axis. `radius` ~55–75 at 1080p, 100
at 4K: `blur.motion` is not resolution-scaled.

A translated clip exposes bare canvas in direct proportion to the travel, and `blur.motion` clamps
to the clip's extent rather than filling the vacated area. So overscan the slivers first with
`set_clip_properties transform {"width":1.6,"height":1.6}`, giving margin `(w0-1)/2 = 0.30` per
side — **travel = (w0-1)/2**. Base top-left is `(1-1.6)/2 = -0.30` (law 2):

```json
{"clipId":"out","property":"position","keyframes":[[0,-0.30,-0.30,"linear"],[2,-0.60,-0.30,"linear"]]}
{"clipId":"in","property":"position","keyframes":[[0,0,-0.30,"linear"],[2,-0.30,-0.30,"smooth"]]}
```

Travel 0.25–0.35 (width 1.5–1.7) is the working band. Past that it costs more resolution than a
2-frame smear is worth.

**Softer variant, no split and no overscan:** with `L` = the outgoing clip's duration in frames, a
`blur` track `[[L-4,0,"linear"],[L-1,45,"linear"]]` out, mirrored `[[0,45,"linear"],[3,0,"smooth"]]`
in, reads as a defocus swish.

## Flash cut

`apply_color`'s `exposure` caps at ±3 EV and cannot be keyframed, so it will not reach white — and
`apply_effect` rejects every `color.*` id outright. Import one matte and reuse it forever:

```json
{"source":{"matte":{"hex":"#FFFFFF","aspectRatio":"Project"}}}
```

`add_clips` it on a **dedicated top video track** — `add_clips` overwrites, so never onto a footage
track — spanning `[C-1, C+3)`, then:

```json
{"clipId":"flash","property":"opacity","keyframes":[[0,0,"linear"],[1,0.9,"linear"],[3,0,"linear"]]}
```

so peak white lands on C. 0.75 is soft; 1.0 only on the hardest hits.

Omitting `trackIndex` auto-creates a *new* top video track on **every** call, so a flash per two
bars becomes a dozen stacked tracks. Omit it on the **first** flash only, read the index from that
mutation's `createdTracks`, then pass that `trackIndex` on every later flash — and batch a whole
section's flashes into one `add_clips` call, one entry per flash.

Cheaper black blink: `fadeOutFrames: 2` on the outgoing clip and `fadeInFrames: 2` on the incoming.
Do **not** include the nested `audio.id` there, or you duck the beat.

## Dissolve

That black blink is a dip to black, not a dissolve. Clips on one track cannot overlap, so lift the
incoming clip a layer and grow the outgoing under it — no `startFrame` moves, nothing downstream
shifts.

If no overlay video track exists, create one by placing the incoming clip's copy with `add_clips`
and `trackIndex` omitted, then read its index from `createdTracks`; `manage_tracks` cannot create a
track. Then `move_clips` the incoming to that track (`toTrack` only, no `toFrame`), extend the
outgoing by N with `set_clip_properties durationFrames` (it needs N frames of source handle), and
set `fadeInFrames: N` on the incoming.

Fades **multiply** existing opacity keyframes, so a punch or shake already on that clip survives.
By hand the equivalent is `opacity [[0,0,"linear"],[N,1,"linear"]]`.

N ≈ 6–12 at 30 fps; 4 reads as a soft cut, past 20 it drags. Fades do not propagate to linked
media — set `fadeOutFrames: N` on the outgoing's nested `audio.id` and `fadeInFrames: N` on the
incoming's, or the picture crossfades over a hard audio butt.

## Speed ramp

There is no keyframable speed. **Length-preserving path** (the default): split into 3–5 segments in
**one** `split_clips` call — splitting after a speed change invalidates every later project frame —
then pass `speed` **and `durationFrames` together` on every segment. Duration wins, each segment
holds its slot, nothing downstream moves, and the locked beat cut survives.

Carry the source through by hand: segment *n*'s `trimStartFrame` = previous `trimStartFrame` +
previous `durationFrames` × previous `speed`.

Use geometric steps: ramp-out `1.0, 0.5, 0.25`; ramp-in `0.35, 0.6, 1.0`, always shorter. Land the
return to 1.0 on a downbeat. No segment under 4 timeline frames; never below 0.25× on 30 fps source.

Only when the montage is *meant* to get longer: push every downstream clip right by the total delta
(`move_clips`, or `insert_clips` to ripple) **before** retiming, then set `speed` alone, right to
left — otherwise a slowed segment grows over its neighbour and `move_clips` eats what it lands on.

Retiming propagates to linked audio: set the partner's `audio.id` to `volumeDb: -60`, or
`manage_clip_links` unlink first. **Never retime the music bed.**

## Shake

Overscan first or rotation and jitter expose black edges. Margin per side is `(w0-1)/2` and you
want at least 2× peak amplitude: **`width` = `height` ≥ 1 + 4 × peakAmplitude**, plus ~0.02 more
when rotation is layered on. So 1.03 covers amplitudes ≤ 0.0075; use **1.06** for the 0.008–0.014
band and **1.08** with rotation.

One `position` track, a new value every 2 frames, `interp: "hold"` (`smooth` turns a shake into a
float), 6–8 rows over 10–16 frames, landing back on base — and base is `(1-w0)/2`, not 0:

```json
{"clipId":"clp_a","property":"position","keyframes":[[0,-0.030,-0.030,"hold"],[2,-0.019,-0.037,"hold"],[4,-0.038,-0.025,"hold"],[6,-0.023,-0.042,"hold"],[8,-0.036,-0.028,"hold"],[10,-0.027,-0.033,"hold"],[12,-0.030,-0.030,"hold"]]}
```

Amplitude 0.004–0.008 is subtle, 0.008–0.014 hard, past 0.020 reads as a codec fault. Never
alternate the same two values — vary magnitude 0.4×–1.0× and flip sign irregularly. Rotation
nudges: `[[0,0,"linear"],[1,-1.1,"hold"],[6,0,"smooth"]]`, ±0.4–1.2°, ceiling 2.0°, one per 8-bar
phrase.

Do not shake a shot that already carries strong camera motion, any shot held over 2 s for its
content, or any clip under a title — a title is a separate clip with its own position and scale
tracks and will **not** inherit the shot's shake. Keyframe both with the same rows, or neither.

## Freeze frame

`capture_frame` the moment, then `add_clips` the returned `mediaRef` as a still on the track above,
starting at that frame. Give it `source: [0, holdSeconds]`. Cut out hard on a downbeat.

## Layout moments

`apply_layout` owns all multi-clip composition. **Never build a split, PIP or grid from
`set_clip_properties` transform/crop or from position/scale keyframes.**

Land in and out on downbeats and cut out hard, never fade. Hold `grid_2x2` or `three_up` one bar, a
`pip_*` reaction half a bar, `side_by_side` two bars.

Mute every non-hero cell's nested `audio.id` at `volumeDb: -60`; keep one at −6 under the music.
Give a PIP inset `edgeRounding: 0.04`, `edgeSoftness: 0.015`.

In 16:9, grid cells and PIP insets crop nothing. `side_by_side` keeps 50% of width (`anchorX`
only), `top_bottom` 50% of height (`anchorY` only), `three_up` 33%. Frame faces with
`anchorX = clamp((faceX - f/2) / (1 - f), 0, 1)` where `f` is the kept fraction.

On a layout clip animate only `opacity` or `blur`. To move a whole composite, build it on its own
timeline with `create_timeline`, nest it as a `sequence` clip, and keyframe the carrier.

## Budget

The cuts carry the rhythm; motion accents it. Per 60–90 s montage:

- **≥ 80% of beat cuts stay hard.**
- One whip per 4 bars, maximum.
- One flash per 2 bars, maximum.
- Punches on downbeats only, roughly one clip in three.
- Shake on no more than 20–30% of clips.
- Two or three layout moments, total.
- Dissolves only where the piece genuinely asks for softness.
