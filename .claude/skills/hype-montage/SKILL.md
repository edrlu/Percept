---
name: hype-montage
description: Cut a high-energy, music-driven montage from personal footage using the Palmier Pro video editor over MCP, fanning out read-only subagents to review every clip and source the music before a single cut is made. Use this skill whenever the user wants a montage, recap, hype video, trip video, sizzle reel, highlight reel, aftermovie, or "something cut to music" out of their own clips — even if they never say "Palmier" — and whenever they ask for beat-synced cutting, transitions, speed ramps, color grading, or titles on real footage. Also use it for any question about driving Palmier Pro from Claude.
---

# Hype montage

Turn a folder of raw personal footage into a beat-cut, graded, titled montage in Palmier Pro,
delegating footage review and music search to read-only subagents.

**Not for** talking-head, interview, or tutorial edits. Those are transcript-driven — reach for
`remove_silence` and `remove_words`, not a beat grid.

## Connect

Palmier Pro is macOS-only (Apple Silicon, macOS 26+) and its MCP server is local. The app must be
**open**; the server lives at `http://127.0.0.1:19789/mcp`.

```bash
claude mcp add --transport http palmier-pro http://127.0.0.1:19789/mcp
```

If the tools are absent, say so and stop — nothing in this skill works without them.

## The eight laws

These are the rules that break naive attempts. Everything else is craft.

1. **Watch before you cut.** Never choose or describe a clip from its filename. No `add_clips`
   entry may carry an in/out point you have not seen as an `inspect_media` frame or a
   `search_media` hit.
2. **One writer.** You hold the write lock on the timeline for the whole job. Every subagent is
   read-only. The undo stack is shared across you, every agent, and the user — a second writer's
   edit landing between your mistake and your `undo` reverts *its* work, not yours.
3. **Frames vs seconds.** Timeline positions are **frames**. Source positions, `detect_beats`
   output and `search_media` hits are **seconds**. Palmier converts with
   `Int(seconds * fps)` — **truncation, not rounding**. Quantise your own in/out points to exact
   frame boundaries (`sec = frame / fps`) so a 2-decimal beat time never lands a frame early.
4. **There is no transition tool.** No cross-dissolve, no whip-pan preset, no transition library.
   Every transition is built from `set_keyframes`, `set_clip_properties`, `apply_effect` and
   `apply_layout`. See `references/motion-recipes.md`.
5. **Patch from the delta.** Call `get_timeline` **once** per session. Every mutation returns
   `clips`, `shifted {track, fromFrame, by, count}`, `removedClipIds`, `createdTracks` and `notes`
   — update your in-memory model from that. Re-read only after `undo`, `create_timeline`,
   `set_active_timeline`, or a failure that suggests staleness.
6. **Batch.** One `add_clips` with all 30 entries, one `split_clips` with all frames. Each batch is
   one undo step; 30 calls are 30 undo steps you cannot unwind cleanly.
7. **Structure before polish.** Pass one is hard cuts at native speed — no effects, no keyframes,
   no grade. Never rig a whip pan onto a cut order you may still rearrange.
8. **Edits are free; four tools are not.** Everything on the timeline is undoable and costs
   nothing — cut it, look at it, `undo` if wrong. Do **not** ask permission for an edit.
   `generate_video`, `generate_image`, `generate_audio` and `upscale_media` cost real money and are
   **not undoable**: confirm `canGenerate: true`, call `list_models`, propose model + duration +
   aspect, and wait for explicit confirmation. A real trip montage should need zero generations.

Ask the user **one** question up front, only if aesthetic direction is genuinely unstated:
*"Chill and scenic, or hype and fast?"* That answer sets shot length, speed range and grade.
**If no runtime is named, cut 75 seconds and say so in one line.** Infer everything else.

## The fleet

| Role | Count | May call | Never calls |
|---|---|---|---|
| **Cutter** (you) | 1 | everything | — |
| Footage scout | 3–4 assets each, 6 at a time | `get_media`, `inspect_media`, `search_media`, `inspect_color` | every mutator |
| Music scout | 2–3 lanes | web search, `get_media`, `inspect_media` | every mutator |
| QC reviewer | 3–4 | `get_timeline`, `inspect_timeline`, `inspect_media`, `inspect_color` | every mutator |

Scouts return structured records; **you** perform every import, placement and edit. Full prompts,
schemas, sharding maths and merge rules: `references/subagent-fleet.md`.

---

## Phase 0 — Set up the project

1. `manage_project` — `action:'list'`, then `'open'` or `'create'`. A session may start with no
   project bound; nothing else works until one is.
2. `create_timeline` — work on a fresh timeline, not the user's. It switches every tool to itself
   and returns its `timelineId` in its own result. Record that id.
3. `set_project_settings` — set `aspectRatio` (16:9 master; see Phase 10 for the vertical version),
   `fps` matching the dominant source footage, and `quality`. Do this **now**: `add_clips` silently
   re-fits the canvas to the first video asset when it lands on an empty timeline.
4. `get_timeline` — your one full read. Record `fps`, `width`/`height`, `totalFrames` and
   `canGenerate`. `timelines` appears only once the project holds more than one; on a fresh project
   take timeline ids from an unfiltered `get_media`.

`manage_tracks` **cannot create a track.** Tracks are created only by omitting `trackIndex` on
`add_clips` / `add_texts`, or by `apply_layout` in `mediaRef` mode. A mutation delta reports
`createdTracks` as `{index, label, type}` rows — there is no `trackId` in a delta, so target
follow-up calls by index and re-read `get_timeline` if you need stable ids.

## Phase 1 — Ingest and understand the footage

`import_media` (url, path or bytes) then `get_media` for the library. Group with `organize_media`
into folder paths (`Trip/Day1`); folders have no ids, they are created on demand.

Scan **coarse to fine**, never clip by clip at full depth:

1. **Cull numerically first** — from `get_media` alone, drop assets under 0.5 s and note every
   asset whose fps or orientation differs from the project.
2. **Storyboard** — `inspect_media` with `overview:true` returns one image for the whole asset.
   This is the cheap pass; run it on everything that survived the cull.
3. **Zoom** — re-call with `startSeconds`/`endSeconds` only on assets the storyboard shows are
   worth it. Windowed calls transcribe only that span, so they are fast.
4. **Search** — `search_media` finds moments by content across the library
   (`scope:'spoken'` for dialogue only). Hits come back as source-second ranges you can pass
   straight to `add_clips` as `source`.

Note: `inspect_media` returns **source seconds** — unless you pass `clipId`, which switches it to
project frames. For picture in/out points, call it without `clipId`.

Build a **coverage map** as you go: people, locations, days, times of day. A friend who appears
zero times is a bug in the edit, not in the footage.

## Phase 2 — Fan out the footage scouts

Hand the scouts **Phase 1's shortlist**, not a fresh `get_media` of the whole library.

- **≤ 12 assets:** review them yourself.
- **12–120 assets:** one scout per 3–4 assets, dispatched 6 at a time. That batch size is an image
  budget, not a guess: one storyboard plus three zooms at `maxFrames: 6` is ~19 images per asset,
  and ~76 is a subagent's practical ceiling. An 8-asset batch gets silently skimmed at the tail.
- **Over 120 assets:** two stages. Stage 1 runs `inspect_media(overview: true)` only, 25 assets per
  scout, returning keep/cut plus an energy score. Stage 2 scores the survivors at 3–4 assets each.

Run one `search_media` yourself before fanning out, to pre-warm the on-device visual model so six
scouts do not each trigger a model install.

Each scout returns one record per asset against `assets/shot-log.schema.json` — in/out points in
source seconds, a `hero`/`keep`/`maybe`/`cut` verdict, a 1–10 energy rating, shot size, motion
direction, subjects, `peakSeconds`, and any diegetic audio worth hearing.

Merge: reject any record whose `mediaRef` is not in your `get_media` list (a scout hallucinated
it), reject any with `reviewed:false`, then rank by verdict then energy. Aim for **~2.5× your
final shot count** in `hero`+`keep` selects. Write the merged result to `selects.json`.

**If you have no file-writing tool**, the markers *are* the artifact: pack each section's rows into
a range marker's `comment` (4,000 chars) via `manage_markers`, and say so to the user once.

## Phase 3 — Fan out the music scouts

**First check you have a web-search tool.** If you do not, skip the fleet: ask the user for a local
path or an HTTPS link to a track they have rights to, or permission to spend on `generate_audio`.
`search_media` only searches the project library — it cannot reach a catalogue or the web.

With web search, run 2–3 scouts on **different lanes** so they are not redundant: a genre/BPM lane,
a licensing lane (royalty-free libraries, platform audio libraries, CC-BY), and a
reference-artist lane. Each returns candidates with title, artist, source URL, licence terms,
claimed BPM and duration.

Licensing is not optional: a commercial hip-hop track will be Content-ID flagged on upload. Say
that plainly once, then work with what is actually licensable.

**You** import the top candidates. Then verify each yourself — `inspect_media` for real duration,
`detect_beats` for real BPM. A scout's claimed BPM is a claim, not a fact. Present the user two or
three verified options with their real numbers and let them pick.

If nothing is licensable and the user approves the spend: `list_models` with `type:'audio'`, then
`generate_audio` with an instrumental flag where supported. Describe style, mood, genre and tempo
in the prompt. It costs money and cannot be undone.

## Phase 4 — Build the spine

**Place the music bed first — it is the first clip on the timeline.** One `add_clips` with
`trackIndex` omitted, `startFrame: 0`. An audio-only first clip takes the safe path through the
canvas check and cannot re-fit your resolution. Leave `volumeDb` alone; level is a Phase 9
decision.

Then `detect_beats` on the bed's `mediaRef`. It returns `beats` and `downbeats` in **source
seconds** plus an estimated `bpm`.

Sanity-check before you build on it: `4 * 60 / bpm` should match the observed gap between
consecutive downbeats. A halved or doubled value means the detector locked to the wrong metrical
level — trust the spacing you can see, not the reported number.

With the bed at `startFrame: 0`, `speed: 1` and no trim, every beat frame is `round(B * fps)`.
Take `startFrame`, `trimStartFrame` and `speed` from the `add_clips` delta rather than your own
arithmetic, and derive **every** cut from its absolute beat time. Never accumulate durations — at
128 BPM / 30 fps that loses 4 frames over 64 beats, which is plainly visible. Full tables and the
general formula: `references/beat-math.md`.

Group downbeats into 4-bar phrases and 8-bar sections. Mark **section boundaries only** with
`manage_markers` — it writes one marker per call, so marking 200 downbeats is 200 round trips.
Eight to sixteen markers is right.

Then write the **paper edit** (`assets/paper-edit.schema.json`): every slot's start/end frame,
mediaRef, source in/out, and a one-line reason. Design the energy curve here —

| Section | Bars | Hold | Feel |
|---|---|---|---|
| Hook | 2–4 | ½–1 bar | best shot cold, or a title on the downbeat |
| Build | 8 | 2 bars | wides, establishers, room to breathe |
| Drop | 8–16 | ½ bar | fastest cutting, highest energy, punch-ins |
| Sustain | 8 | 1 bar | the body of the trip |
| Breakdown | 4–8 | 2–4 bars | slow-mo, the emotional shot, golden hour |
| Peak | 8 | ½ bar | the hero shot lands on the last downbeat |
| Resolve | 2–4 | 4 bars | one held shot, music ends on its own resolution |

If a slot has no reason, it is filler — find a better shot. `inspect_timeline` cannot help yet:
with only the bed placed it errors *"No video track available in timeline."*

## Phase 5 — Assemble

Place picture in **one batched `add_clips`, `trackIndex` omitted on every entry** — that
auto-creates the video track (and the audio track its linked nat sound lands on). Mixing entries
that specify `trackIndex` with entries that omit it is rejected. Read the real indexes back from
`createdTracks` and use them for everything after.

Place with `source: [srcIn, srcOut]` from the paper edit, plus `startFrame`. `source` is
frame-exact, and unlike `endFrame` it preserves your in-point rather than forcing
`trimStartFrame: 0`.

```json
{"entries": [
  {"mediaRef": "m_a1b2", "startFrame": 0,   "source": [12.40, 14.30]},
  {"mediaRef": "m_c3d4", "startFrame": 57,  "source": [3.10, 5.00]}
]}
```

Clips on the same track are sequential — an overlapping placement trims or removes what is already
there, exactly like dragging in the UI.

Three rules for the cut itself:

- **Vary the hold.** A montage of identical-length shots reads as mechanical. Vary around each
  section's nominal hold; the fastest cutting belongs at the drop, not throughout.
- **Land the peak on the beat, not the cut.** Set the in-point so the moment's `peakSeconds`
  falls on the downbeat, then let the shot start a few frames earlier. Cutting exactly on every
  beat with no lead feels robotic.
- **Cut on matching motion.** A shot moving left cuts cleanly into another moving left. Use the
  shot log's `motionDirection` to order adjacent slots.

Nothing below 8 frames at 30 fps reads as a shot — that is a flash, legitimate at a drop only.
Lock the cut before any polish. Watch it once end to end.

## Phase 6 — Motion, transitions, effects

Per clip, the order is fixed: `split_clips` → `set_clip_properties` → `apply_layout` →
`set_keyframes`. **`apply_layout` clears the clip's position, scale, rotation and crop keyframe
tracks** (opacity and volume survive), so re-running a layout to nudge `anchorX` destroys any
motion rig on that clip.

Three facts the recipes depend on:

- Keyframe frames are **clip-relative** (0 = the clip's first frame), so they travel with the clip.
- `position` is the clip's **top-left** in 0–1 canvas coords, not its centre. `scale` is normalized
  **width and height**, not a multiplier.
- A `scale` track alone stays centred on the clip's static centre — which is usually what you want.
  Add a `position` track only for an off-centre punch, or to overwrite a stale one.
- Read the clip's base extent first: `get_timeline` omits only an *identity* transform, so any clip
  whose source aspect differs from the canvas reports `transform.width`/`height`. Every scale row
  is `[frame, w0 * factor, h0 * factor]`, defaulting `(w0, h0)` to `(1, 1)` when absent.

Recipes for punch-in, whip pan, flash cut, dissolve, speed ramp, shake, freeze and grid moments:
`references/motion-recipes.md`. Valid effect ids, layouts and keyframe rows:
`references/effects-layouts-keyframes.md`.

**Restraint.** Effects go on the slots the paper edit marked, nowhere else. If every cut has a
whip, none of them read. Three or four motion moments in a 75-second piece is plenty.

## Phase 7 — Color

Grade one hero shot first, then propagate.

`apply_color` merges knob by knob — pass one value, the rest survive. Iterate against
`inspect_color`: grade, look, read the gap, adjust. Its knobs are `exposure`, `contrast`,
`saturation`, `vibrance`, `temperature`, `tint`, `highlights`, `shadows`, `blacks`, `whites`, the
wheels (`shadowsHue`/`shadowsAmount`/`shadowsLum`, `midsHue`/`midsAmount`/`midsGamma`,
`highsHue`/`highsAmount`/`highsGain`), curves, hue curves and a LUT.

Match shots in this order: exposure → white balance → contrast → saturation → look. Then read the
hero's returned `color` object and fan it out to every other video clip in **one**
`apply_color {clipIds: [...], color: {...}}` call.

**There is no adjustment layer.** `apply_color`, `inspect_color` and `apply_effect` all refuse
clips with `mediaType: 'sequence'`, so you cannot grade a nested timeline as one object. And
`apply_effect` hard-rejects every `color.*` id — grading only ever happens through `apply_color`.

Guardrails: keep `saturation` ≤ 1.25, `contrast` ≤ 1.2, wheel amounts ≤ 0.15, and never let skin go
orange chasing a warm sky. Full recipe: `references/color-text-mix.md`.

## Phase 8 — Text

`add_texts` for authored overlays; `add_captions` transcribes spoken audio (which a music montage
usually does not want). Time entrances to downbeats.

Fewer cards is better: an opening title, one or two location cards, and nothing else. Animate with
`scale` and `blur` keyframes over 4–8 frames — text clips use the same keyframe tracks as video.
Keep text inside the middle 80% of the frame; a 9:16 version will have platform UI over the top and
bottom sixth. Styling detail: `references/color-text-mix.md`.

## Phase 9 — The mix

This is the **only** place the bed's level is committed — once volume keyframes exist,
`set_clip_properties volumeDb` clears them, so set level first, then write the ducks.

- Music bed: `volumeDb: 0`. Never above `+3`.
- Nat sound, by default: `-60` (mute) or `-18` for texture. Every video clip's linked audio is
  folded into it as `audio: {id, ...}` — address it by that nested id.
- A moment worth hearing (a scream, a laugh, a splash): bring that clip's audio to `-6` to `-3`,
  and duck the bed to `-6` underneath with `volumeDb` keyframes, 6 frames down and 6 back up.
- Put a 2–3 frame fade on every audio edit. Clicks are the single most common giveaway.
- Fades do **not** propagate to a linked partner — pass both the video clip id and its nested
  `audio.id` to fade picture and sound together.

End on the music's own resolution, not a hard stop.

## Phase 10 — QC, versions, export

Review with `inspect_timeline`, which composites what the viewer actually sees (`get_timeline` only
confirms structure). It samples at most `maxFrames` frames across a range — default 6, **max 12** —
so bracket the span you care about rather than sampling the whole piece.

Fan out 3–4 QC reviewers on separate lenses — rhythm, coverage, color consistency, text and audio —
each returning structured defects. Then check yourself:

- [ ] Every cut lands where the paper edit said, and the peaks hit their downbeats.
- [ ] No shot under 8 frames that is not a deliberate flash.
- [ ] Every person in the coverage map appears at least twice.
- [ ] No unexplained `hero` select left unused.
- [ ] Grade is consistent across shots from different phones.
- [ ] Text is legible and inside the safe area.
- [ ] No audible clicks; the bed never buries a moment that should land.

**Vertical version, in this order:** `create_timeline(from: <master>)` → `set_project_settings
{aspectRatio: '9:16'}`, which re-fits every clip → reframe the re-fitted clips with `apply_layout`
in `clipIds` mode, biasing `anchorX`/`anchorY` so faces survive the crop. Copying ids across
timelines does not work: every clip and track id in the copy is new, so re-read `get_timeline`
first.

`export_project` — `mode` is `video` (default), `xml`, `fcpxml` or `palmier`; `codec` is `H.264`,
`H.265` or `ProRes`; `resolution` is `720p`, `1080p`, `2K`, `4K` or `Match Timeline`. Omit
`outputPath` unless the user named one (defaults to `~/Downloads`). Every export is queued in the
background — report that it started, then use `manage_exports` to read progress. Never infer a
stuck export from elapsed time.

---

## References

| File | Read it when |
|---|---|
| `references/palmier-tools.md` | You need exact parameters for any of the 49 tools |
| `references/effects-layouts-keyframes.md` | Effect ids, layout slots, keyframe row layouts |
| `references/motion-recipes.md` | Building any transition or motion effect |
| `references/beat-math.md` | Converting beats to frames; BPM tables |
| `references/subagent-fleet.md` | Spawning scouts: prompts, schemas, sharding, merge |
| `references/color-text-mix.md` | Grade, text styling and mix values |
| `assets/shot-log.schema.json` | What footage scouts return |
| `assets/paper-edit.schema.json` | The cut plan |

## Anti-patterns

- Choosing a clip from its filename.
- Letting a subagent write to the timeline.
- Calling `get_timeline` after every edit instead of patching from the delta.
- Accumulating frame durations instead of deriving each cut from its beat time.
- Reaching for a transition tool. There isn't one.
- Running `apply_layout` after `set_keyframes` and wondering where the motion went.
- Grading before the cut is locked.
- Putting an effect on every cut.
- Asking permission for a free, undoable edit — or spending money on a generation without asking.
- Busy-polling a generation. Hand back the placeholder id and stop.
- Narrating each step. The user is watching the timeline change; lead with the outcome in a
  sentence or two.
