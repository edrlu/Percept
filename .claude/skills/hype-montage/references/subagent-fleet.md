# The subagent fleet

You are the **Cutter**. You hold the write lock on the timeline for the whole job and perform every
import, placement and edit. Scouts are read-only and exist to buy parallel coverage of footage and
music that you would otherwise read serially.

## Why mutation is never parallelised

- The undo stack is **shared** across you, every agent and the user. A scout's write landing between
  your mistake and your `undo` reverts *its* work.
- `create_timeline`, `set_active_timeline` and `manage_project` are **global** — a stray switch
  silently redirects every other agent's calls.
- Mutations return **deltas**. `insert_clips` ripples every downstream clip; two writers invalidate
  each other's cached frames and ids instantly.

## The verbatim deny-list

Put this in every scout prompt. The traps look harmless:

> Call no tool other than the ones listed for your role. Calling any of these is task failure:
> `add_clips`, `insert_clips`, `move_clips`, `remove_clips`, `split_clips`, `ripple_delete_ranges`,
> `swap_clip_media`, `set_clip_properties`, `set_keyframes`, `copy_clip_settings`, `apply_layout`,
> `apply_color`, `apply_effect`, `add_texts`, `update_text`, `add_captions`, `manage_tracks`,
> `manage_markers`, `manage_clip_links`, `sync_clips`, `manage_multicam`, `change_cam`,
> `capture_frame`, `import_media`, `organize_media`, `get_transcript`, `undo`, `create_timeline`,
> `set_active_timeline`, `manage_project`, `set_project_settings`, `export_project`,
> `manage_exports`, `generate_video`, `generate_image`, `generate_audio`, `upscale_media`.

Three that deserve naming because they read as harmless:

- `capture_frame` and `import_media` create undoable library assets.
- `get_transcript` is **timeline**-scoped, not asset-scoped, and `remove_words` reuses its
  provider/track selection — a parallel call corrupts your word indices. Scouts read transcripts
  from `inspect_media` instead, which returns source seconds.
- `undo` pops the single shared stack.

---

# 1. Footage scouts

## Shard the library

Call `get_media` **once** yourself and slice its rows. Each scout receives a closed list of
`{mediaRef, durationSeconds, fps, hasAudio, type}` with ids **verbatim** — Palmier ids are short
prefixes and must be passed back exactly, never padded or completed.

**Do not issue `name`.** The filename is the one input that lets a scout describe footage it never
watched.

- **Filter out `type == 'image'`.** `inspect_media`'s `overview` is video-only and stills have no
  `durationSeconds`. Log stills yourself as `class:'texture'` rows with `inSeconds: 0` and a chosen
  display length.
- **Batch: 3–4 assets, or ~8 minutes of source, whichever comes first.** Budget ≤ 4
  image-returning calls per asset (1 overview + ≤ 3 zooms at `maxFrames: 6`) ≈ 19 images per asset,
  so 60–76 images per batch — one subagent's practical ceiling. Drop to `maxFrames: 4` if a batch
  would exceed ~80. An 8-asset batch gets silently skimmed at the tail.
- **Concurrency: 6.** Above that Palmier's on-device visual indexing queues and wall-clock stops
  improving. Dispatch 24 assets as 8 scouts in waves of 6, then 2.
- **Over ~120 assets, go two-stage.** Stage 1 scouts run `inspect_media(overview: true)` only — 1
  image per asset, so 25 assets per scout is fine — and return keep/cut plus one energy score.
  Stage 2 scores the ~25% survivors at 3–4 assets each. The 3–4 cap applies only to depth passes.
- **Pre-warm the visual model**: run one `search_media` yourself with a caption-style query before
  fan-out, so six agents do not each trigger a model install.
- Never batch the music track with footage. `detect_beats` runs once, in you.

## Scout prompt template

> You are a footage-review subagent for a high-energy montage cut in Palmier. You are READ-ONLY;
> another agent owns the timeline.
>
> Brief: `{{BRIEF}}`. Project fps: `{{FPS}}` — context only, never report frames.
> Your assets. Use these ids exactly as written; never invent, lengthen or complete one:
> `{{MANIFEST_SLICE}}`
>
> Method, per asset, in order:
> 1. `inspect_media(mediaRef, overview: true)` — one storyboard of timestamped moments. Few tiles on
>    a long clip means static footage; say so and move on.
> 2. Pick at most 3 promising windows. For each:
>    `inspect_media(mediaRef, startSeconds, endSeconds, maxFrames: 6)`. Frames carry a 0–1 grid,
>    origin top-left — read `subjectCenter` off the grid, do not eyeball it.
> 3. Optional gap-fill: `search_media(query, mediaRef, scope: 'visual', limit: 10)` with
>    caption-style queries ("a person jumping off a pier into the ocean"). A hit is a 2–6 s
>    *neighbourhood*, not a shot — re-inspect before logging. Scores are uncalibrated: ordering
>    only.
> 4. `inspect_color(mediaRef)` on anything that looks blown or crushed. Report the measured
>    `clipping` percentages alongside the `exposureEv` you would apply.
>
> Hard rules:
> - **Never describe an asset from its filename.** `beach_jump_4.mov` is evidence of nothing.
> - All times are **SOURCE SECONDS**, 2 dp. Never multiply by fps. Never report a frame number.
> - Log **moments, not files**: `outSeconds − inSeconds` between 0.6 and 3.0. Return 2–4 per asset,
>   6 maximum.
> - Leave handles: ≥ 0.40 s of usable media beyond each end. Report `handleIn` / `handleOut`.
> - Every moment cites `framesSeen` — timestamps you actually looked at. If you cannot verify it,
>   omit it.
> - If `search_media` returns an `index` object, set `indexIncomplete: true` and report its status.
>   `disabled` / `failed` mean visual search will not complete — report once; spoken results still
>   work. Absence of results is not absence of footage. Do not poll.
> - `{{DENY_LIST}}`
>
> Return the JSON object only. No prose, no fence.

## Shot-log row

One row per **moment**, per `assets/shot-log.schema.json`. The richer per-moment form the scouts
return adds these fields, which the assembler relies on:

```jsonc
{
  "shotId":       "m_9f2#2",     // stable handle used in markers and cut order
  "mediaRef":     "m_9f2",       // EXACT id from the issued list
  "inSeconds":    12.40,
  "outSeconds":   13.95,         // out−in in 0.6..3.0
  "handleIn": 0.60, "handleOut": 0.85,   // spare media each side, >= 0.40
  "energy":       8,             // 0-10, drives on-screen duration
  "class":        "air",         // laugh|air|impact|reveal|group_to_cam|reaction|texture
  "peakSeconds":  13.10,         // the frame the cut must land on
  "motion":       "pan_L",       // pan_L|pan_R|tilt_U|tilt_D|push_in|pull_out|orbit|handheld|static
  "motionSpeed":  2,             // 0-3; 3 is already a whip, do not add one
  "framing":      "medium",      // wide|medium|close|detail|aerial
  "subjectCenter":[0.42,0.55],   // subject in the SOURCE frame, off inspect_media's 0-1 grid.
                                 // NOT a canvas position — never pass to set_keyframes position
                                 // (which takes the clip's TOP-LEFT). For apply_layout anchors,
                                 // or convert: topLeftX = subjectX - width/2.
  "faces":        3,
  "shake":        1,             // 0-3; >=2 caps on-screen duration at ~0.5s
  "speedable":    1.5,           // max safe set_clip_properties speed
  "exposureEv":  -0.3,           // apply_color exposure units, -3..+3
  "clipping":     {"black":0.4,"white":2.1},   // % straight from inspect_color
  "audio":        "duck",        // keep|duck|mute
  "verdict":      "keep",        // keep|maybe|cut
  "confidence":   0.8,
  "framesSeen":   [12.4,13.1,13.9],
  "label":        "Kai clears the pier rail, spray behind"    // <= 12 words
}
```

## Merge, validate, rank

Scouts hallucinate. **Drop, never repair:**

1. Id is not an exact string match in the issued manifest → drop. No prefix completion, no fuzzy
   matching.
2. Range violates `0 ≤ in < out ≤ durationSeconds`, or length outside 0.6–3.0 s → drop.
3. **Unit drift** — a "seconds" value implausible against `durationSeconds` but plausible as frames
   means the scout returned frames. Drop and re-ask. Never silently divide by fps; that corrupts all
   downstream beat maths.
4. **Coverage diff** — `assetsReviewed` ∪ `assetsSkipped` must equal the issued list. Re-dispatch
   only ids in neither.
5. **Empty or prose return** — retry once at half batch size. Second failure marks those assets
   `uncovered`, and you tell the user. Never synthesise shot times you did not receive.
6. **Over 30% of one agent's rows fail** → discard that whole batch and re-run its assets singly.

Then dedupe and rank:

- Cluster same-`mediaRef` moments with interval IoU ≥ 0.5. The survivor keeps the in/out and
  `peakSeconds` of its highest-`energy` member, takes max energy, and sets `agreement` = cluster
  size.
- `finalScore = 0.6*(energy/10) + 0.2*confidence + 0.2*min(agreement,3)/3`.
- **Cross-asset dupes** (three phones, one jump) are not caught by that. Flag same-`class` +
  same-`label` rows across mediaRefs as a `dupeCluster`; keep the highest `finalScore` and demote
  the rest. Never place two members of one cluster within 8 s of each other. If the user genuinely
  wants a multi-angle beat, that is `manage_multicam` or `sync_clips`, not a dedupe step.
- **Diversity caps**: at most 3 selects per `mediaRef` in the top 20; at most 2 per asset per 60 s
  of finished montage. Count appearances per person — one friend at 14 shots and another at 3 reads
  as an accident.
- **Volume**: `ceil(2.5 × runtime / mean(outSeconds − inSeconds))`. A 75 s montage at ~1.4 s average
  needs ~135 moments plus a maybe-pile.
- **Spot-verify the top 12 yourself** with `inspect_media(mediaRef, startSeconds, endSeconds,
  maxFrames: 3)`. A row whose frames do not show the claimed action is a hallucination — demote it
  and distrust that agent's whole batch.

## Persist the selects

Write the merged, ranked log to a file before doing anything else — it survives context loss and is
the sole input to the spine and assembly phases:

```
selects.json → { brief, fps, keeps: [...], maybes: [...],
                 dupeClusters: [[shotId,...]], uncovered: [{mediaRef, reason}] }
```

**If you have no file-writing tool**, the markers *are* the artifact: pack each section's rows into
a range marker's `comment` (4,000 chars) with `manage_markers`, re-read them with a windowed
`get_timeline` after any ripple, and tell the user once that the plan lives in the markers.

Do not carry the log in context and re-`inspect_media` while cutting — that burns the window and
drifts. **One review pass:** once an asset's rows are validated, do not reopen it.

---

# 2. Music scouts

**Check first that you have a web-search tool.** `search_media` only searches the project library;
it cannot reach a catalogue or the web. Without web search, skip the fleet — ask the user for a
local path or an HTTPS link to a track they have rights to, or for permission to spend on
`generate_audio`, and go straight to verification.

## Lanes

Two or three scouts, each on a **different lane** so they are not redundant:

| Lane | Brief |
|---|---|
| Genre / BPM | Tracks matching the genre and a stated BPM band, with an early drop |
| Licensing | Royalty-free libraries, platform audio libraries, CC-BY — licence terms first |
| Reference artist | "Sounds like X" without being X |

Each returns: `{title, artist, sourceUrl, licence, licenceAllowsSocial, claimedBpm,
claimedDurationSeconds, structureNotes, whyItFits}`.

## The music brief

Give every lane the same brief so candidates are comparable: genre and sub-genre, BPM band, energy
arc, instrumentation, vocal or instrumental, two reference artists, target duration, and whether
explicit lyrics are acceptable.

State the licensing reality once, plainly: a commercial hip-hop track will be Content-ID flagged on
upload. Then work with what is actually licensable.

## Verify — you, not a scout

Scouts propose; you verify, because verification requires `import_media`, which is a write.

1. **You** `import_media` the top candidates and `organize_media` them into `Music/Candidates`.
2. Hand the resulting mediaRefs to one read-only verifier, or check them yourself:
   `inspect_media` for the real duration, `detect_beats` for the real BPM and the downbeat spacing.
3. A claimed BPM is a claim. `4 * 60 / bpm` must match the observed downbeat gap.
4. Reject anything that cannot actually be obtained, or whose licence does not permit the user's
   intended platform.
5. Present two or three verified options with their real numbers and let the user pick.

If nothing is licensable and the user approves the spend: `list_models` with `type: 'audio'`, then
`generate_audio` with an instrumental flag where supported. It costs real money and cannot be
undone.

---

# 3. QC reviewers

Three or four read-only reviewers over the locked cut, each on **one lens** so they do not all
report the same thing:

| Lens | Looks at |
|---|---|
| Rhythm | Do cuts land on their beats? Is shot length varied? Does the drop cut faster than the build? |
| Coverage | Does every person appear? Any unused `hero` select? Any location missing? |
| Colour | Consistency across shots from different phones; clipping; skin tone |
| Text & audio | Legibility, safe areas, clicks, the bed burying a moment |

Each may call `get_timeline`, `inspect_timeline`, `inspect_media`, `inspect_color` — nothing else.

Give each reviewer the frame ranges to sample. `inspect_timeline` returns at most `maxFrames`
frames per call (default 6, **max 12**), so a reviewer must bracket spans rather than ask for the
whole piece.

Each returns defects as `{frameRange, lens, severity, problem, suggestedFix}`. You decide what to
act on; a reviewer never fixes anything.
