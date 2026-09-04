# Palmier Pro — full MCP tool reference

Extracted verbatim from `ToolDefinitions.swift` in the Palmier Pro v0.7.6 GPL source
(`github.com/palmier-io/palmier-pro`). These are the 49 tools the editor exposes at
`http://127.0.0.1:19789/mcp`. If a tool is not on this list, it does not exist — do not call it.

Read this file when you need exact parameter names, units, or ordering constraints for a tool.
For effect ids, layout names and keyframe property layouts see `effects-layouts-keyframes.md`.

## Contents

- [`get_timeline`](#get-timeline)
- [`inspect_timeline`](#inspect-timeline)
- [`create_timeline`](#create-timeline)
- [`set_active_timeline`](#set-active-timeline)
- [`manage_markers`](#manage-markers)
- [`set_project_settings`](#set-project-settings)
- [`export_project`](#export-project)
- [`manage_exports`](#manage-exports)
- [`get_media`](#get-media)
- [`inspect_media`](#inspect-media)
- [`search_media`](#search-media)
- [`import_media`](#import-media)
- [`capture_frame`](#capture-frame)
- [`organize_media`](#organize-media)
- [`add_clips`](#add-clips)
- [`insert_clips`](#insert-clips)
- [`move_clips`](#move-clips)
- [`remove_clips`](#remove-clips)
- [`manage_clip_links`](#manage-clip-links)
- [`manage_tracks`](#manage-tracks)
- [`split_clips`](#split-clips)
- [`ripple_delete_ranges`](#ripple-delete-ranges)
- [`swap_clip_media`](#swap-clip-media)
- [`set_clip_properties`](#set-clip-properties)
- [`copy_clip_settings`](#copy-clip-settings)
- [`set_keyframes`](#set-keyframes)
- [`apply_layout`](#apply-layout)
- [`sync_clips`](#sync-clips)
- [`manage_multicam`](#manage-multicam)
- [`change_cam`](#change-cam)
- [`get_multicam`](#get-multicam)
- [`undo`](#undo)
- [`get_transcript`](#get-transcript)
- [`remove_words`](#remove-words)
- [`remove_silence`](#remove-silence)
- [`detect_beats`](#detect-beats)
- [`add_texts`](#add-texts)
- [`update_text`](#update-text)
- [`add_captions`](#add-captions)
- [`apply_color`](#apply-color)
- [`apply_effect`](#apply-effect)
- [`inspect_color`](#inspect-color)
- [`denoise_audio`](#denoise-audio)
- [`list_models`](#list-models)
- [`generate_video`](#generate-video)
- [`generate_image`](#generate-image)
- [`generate_audio`](#generate-audio)
- [`upscale_media`](#upscale-media)
- [`send_feedback`](#send-feedback)

---

Always call at the start of a session. Returns project settings (fps, resolution, totalFrames, durationSeconds), tracks with a stable trackId, their current index (what every trackIndex parameter takes), type, and clips, plus canGenerate (if false, generation/upscale tools will fail — tell the user to sign in to Palmier and subscribe before attempting them). Clip ids are accepted by clip mutation tools; trackId is accepted by manage_tracks.

Every clip occupies frames: [start, end) — timeline frames, end exclusive, duration = end − start. gaps on a track lists its empty [start, end) spans; no gaps key means contiguous. A video clip's linked audio partner is folded into it as audio: {id, track, …} carrying only what deviates (volumeDb, effects, differing trims); the partner is not repeated on its own track, which instead reports linkedClips (its folded count). Address the audio side by its nested id.

Fields equal to their defaults are omitted: mediaType 'video', sourceClipType = mediaType, speed 1, volumeDb 0, opacity 1, edgeRounding 0, edgeSoftness 0, trims/fades 0, identity transform/crop, default textStyle, track muted/hidden false. Text clips never report trims. Keyframe tracks that animate nothing are shown as what they are: identity tracks are dropped, constant ones appear as the static field (e.g. crop: {left: 0.31}). A graded clip carries `color` — its grade in apply_color's own vocabulary, pasteable to other clips via apply_color's color parameter. Other effects appear as effects: [{type, params}], the exact shape apply_effect accepts.

Caption clips (sharing a captionGroupId) come back per track as captionGroups summaries: clipCount, frameRange, shared style, and a textPreview — individual caption clips and their ids are NOT listed. That summary is all you need to restyle (update_text with captionGroupId) or judge coverage; the spoken words live in get_transcript. Only when you must touch individual caption clips (retime one, delete one, fix one word's style), re-read with captionDetail:true — ideally windowed — to get [clipId, startFrame, endFrame, text] rows, capped at 200 per group. Caption clips whose properties deviate from the group always appear individually in clips.

markers contains persistent review notes with markerId, name, comment, color, startFrame, endFrame, and durationFrames. Point markers have durationFrames 0; range markers use half-open [startFrame, endFrame). Windowed reads include only markers in or intersecting the window.

---

## `inspect_timeline`

See the composited timeline — what the user actually sees in the preview at a given frame: all video tracks stacked with their transforms, opacity, crop, edge softness, edge rounding, and keyframes applied, plus text and caption overlays baked in. Use this to verify your edits landed (a PIP's position, a title's placement, layer order) — inspect_media shows the raw source asset, not the cut.

Frames are project frames (from get_timeline). Pass a single startFrame for one composited frame; add endFrame to sample maxFrames evenly across [startFrame, endFrame) for a transition or sequence. Frames past content render black. Each image carries a 0–1 coordinate grid over the canvas (origin top-left) and its frame number burned into the top-left (f157). Metadata lists, per rendered frame, the clip ids visible on screen top-down (caption clips as their captionGroupId) — so what you see maps straight back to the clips to edit.

---

## `create_timeline`

Creates a timeline and switches to it — every read and edit tool now targets it. Without 'from', the new timeline is empty and inherits fps/resolution from the previously active one. With 'from', it's a full copy of that timeline — the versioning primitive: copy, then edit the copy ("a tighter cut", "a 9:16 version") while the original stays intact; every clip and track id in the copy is NEW, so re-read get_timeline before editing. Undoable.

Use timelines to organize a project: alternate versions, sections assembled separately, or reusable groups. A timeline can be placed inside another as a single clip (add_clips with the timelineId as mediaRef); it then appears as a clip with mediaType 'sequence'.

---

## `set_active_timeline`

Switches the active timeline — the one every read and edit tool targets and the one the user sees. get_media lists the project's timelines (with timelineId). Always re-read get_timeline after switching; clip and track ids from the previous timeline are no longer valid targets.

To edit the contents of a nested timeline (a clip with mediaType 'sequence'), switch to its mediaRef.

**Required:** "timelineId"

---

## `manage_markers`

Creates, updates, or deletes one persistent timeline marker. A zero duration marks one frame; a positive duration is half-open. Status tracks the review workflow: open is awaiting work, review is ready for user approval, and resolved is accepted. Set review only after applying and verifying the requested edit. Set resolved only when the user explicitly approves or requests it.

**Required:** "action"

---

## `set_project_settings`

Change the project's frame rate, resolution, or aspect ratio. Pass fps, explicit width+height, aspectRatio, or quality. aspectRatio accepts presets or a custom width:height value and preserves the current short-edge resolution unless quality is also supplied. Explicit width/height can't be combined with aspectRatio or quality. The timeline's existing clips are re-fitted automatically: auto-fit transforms recalculate for the new canvas size, and all frame positions/durations rescale when fps changes. Undoable.

---

## `export_project`

Queues an export from the current project using the same modes as the Export dialog. mode defaults to video. video renders H.264, H.265, or ProRes; xml writes XMEML timeline XML; fcpxml writes FCPXML; palmier writes a self-contained .palmier project package. For timeline interchange, pick the format by the target editor: Premiere Pro -> xml; DaVinci Resolve or Final Cut Pro -> fcpxml (fcpxml also carries text, transforms, crop, opacity, and keyframes that xml cannot). Video exports render edge softness and edge rounding, Palmier project exports preserve them, and xml/fcpxml interchange omits them. Omit outputPath to write a unique file to ~/Downloads. Existing direct outputPath files are overwritten by default to match the UI save flow; pass overwrite=false to refuse. Every mode returns status=started or status=queued with a jobId and destination path. Use manage_exports to check progress, warnings/results, or cancel by jobId; agent exports post a system notification on completion or failure.

---

## `manage_exports`

Lists or cancels exports for the current project. action=list returns newest first with jobId, filename, path, status, progress percent, and any warnings/result. action=cancel requires the exact jobId returned by export_project or list; a waiting job is removed from the queue and an active job begins canceling. Cancel only when the user asks, or to undo an export just queued with incorrect settings. Never infer that an export is stuck from elapsed time alone.

**Required:** "action"

---

## `get_media`

The library inventory: media assets, folders, and timelines. Call before referencing any asset — every mediaRef in other tools comes from the asset ids returned here. Assets report name, type, durationSeconds, width/height/fps, hasAudio, folder path, and (for AI-generated assets) the generation prompt as a content hint. generationStatus appears only while an async generation/import is unresolved (preparing | generating | downloading | failed) — its absence means the asset is ready.

Filters: ids (poll specific placeholders cheaply), folder (a path; includes subfolders), pending:true (only unresolved generations/imports). Filtered reads return just the matching assets; unfiltered reads also include folders (as paths) and timelines.

---

## `inspect_media`

Look at a media asset before referencing or editing it. Images: the image plus dimensions and EXIF. Video: sample frames plus a transcription of the audio track. Audio: transcription. Lottie: frames sampled evenly across the animation (over gray), plus framerate and duration — use this to verify a Lottie you wrote looks and moves right. Sampled frames and stills overlay a 0–1 coordinate grid (origin top-left). Overview storyboards do not include the grid. Transcription is sentence-level segments — [text, start, end] tuples, capped at 400 — in source seconds, or project frames when clipId is set. When capped, pass the returned nextStartSeconds as startSeconds for the next page.

Long media: pass overview=true for a one-image storyboard, read the segments, then re-call with startSeconds/endSeconds to zoom — windowed calls only transcribe that span, so they are fast.

**Required:** "mediaRef"

---

## `search_media`

Search the media library by content: what's on screen (visual) and what's said (spoken). Visual matching is semantic and on-device — phrase the query like an image caption ('a wide shot of a harbor at sunset'), not keywords; covers videos and stills. A visual search automatically installs a missing on-device model. Spoken matching layers exact keywords over on-device semantic matching of transcript segments — quote the words said, or paraphrase them; transcripts are created automatically while indexing (and by inspect_media and add_captions), so coverage grows as indexing completes. The two groups rank independently and are never blended. Scores are uncalibrated — use them for ordering only.

Hits are source-second ranges (image hits have no time range). To place exactly that moment, pass [startSeconds, endSeconds] straight to add_clips as source — no unit conversion.

An `index` object appears only while it can explain missing results (status: indexing | downloadingModel | preparing | disabled | failed, with indexedAssets vs indexableAssets). modelDownloadProgress is 0...1 while downloading; modelDownloadError reports the current failure. When present, moments may be incomplete — report that instead of concluding the footage doesn't exist, and don't poll in a loop. No index key means visual search was complete. Spoken results work regardless.

**Required:** "query"

---

## `import_media`

Imports external media into the project's library — the bridge for assets coming from other MCP servers (stock libraries, music services, web search) or local files the user already has. The 'source' object must set exactly one of: url (HTTPS only — downloaded in the background, the dominant case; max 1 GB), path (absolute local file path — referenced in place and not copied into the project; may also be a directory, which is imported recursively, mirroring its subfolder structure as media folders), bytes (base64-encoded inline data — max ~15 MB of base64 ≈ 11 MB binary; use url/path for anything larger), or matte (a generated solid-color PNG). For url, type is inferred from the URL path's file extension unless source.mimeType is set as an override (needed for signed URLs whose path has no usable extension). For bytes, source.mimeType is required.

Supported types and extensions: video (mov, mp4, m4v), audio (mp3, wav, aac, m4a, aiff, aifc, caf, flac), image (png, jpg, jpeg, tiff, heic), subtitle (srt, vtt — becomes a subtitle asset; place its cues as caption clips at their timecodes via add_captions subtitleMediaRef or a user drag onto the timeline; not placeable via add_clips). Anything else is rejected — the caller must transcode externally.

URL imports run in the background and return {mediaRef, status:'downloading'} — poll get_media with ids:[mediaRef] until generationStatus clears, then the asset is usable in add_clips. Path, directory, bytes, and matte imports finish inline with status:'ready'. Costs nothing.

**Required:** "source"

---

## `capture_frame`

Capture one video frame as a full-resolution PNG media asset. Use timelineFrame to capture the active timeline's final composited image, including transforms, crop, edge softness, edge rounding, color, effects, text, and captions. Use mediaRef with sourceSeconds to capture an unedited frame directly from a source video instead. Pass the asset's durationSeconds as sourceSeconds to capture its final decodable frame. Exactly one mode is allowed. The returned mediaRef is ready for add_clips, generate_video startFrameMediaRef/endFrameMediaRef, generate_image references, or inspect_media. Every call creates one new undoable media asset.

---

## `organize_media`

Reorganizes the library in one undoable action: create folders, move items into folders, rename items, delete items. An item is a media asset id (from get_media), a timelineId, or a folder path like 'B-roll/Sunset' — the tool tells them apart. Folders are always addressed by path, never by id; destination paths are created if missing. Arrays apply in order (createFolders, moves, renames, deletes), but item references resolve against the library as it was before the call — only 'into' destinations may name folders the same call creates.

Deleting an asset also removes every clip referencing it (reported as clipsRemoved). Deleting a folder deletes its subfolders and assets; timelines inside move to the root instead. Deleting a timeline leaves nest clips referencing it rendering black (a warning reports how many); the last remaining timeline can't be deleted. Returns only what actually happened — createdFolders, moved, renamed, deleted, clipsRemoved, warnings.

---

## `add_clips`

Places one or more media assets on the timeline as a single undoable action. Each entry's asset type must be compatible with its target track (video/image are interchangeable across video/image tracks; audio requires an audio track). When a video asset with audio is placed on a video track, a linked audio clip is automatically created on an audio track (an existing one if available, otherwise a new one). The whole batch is one undo step.

trackIndex is optional. Omit it on all entries and the tool auto-creates the needed tracks — one shared video track for visual entries (above existing visuals) and one shared audio track for audio entries (appended below existing audio, so linked dialogue on A1 stays put and music/VO land on A2+). To target existing tracks, set trackIndex on every entry. Mixing (some entries specify, others omit) is rejected — split into two calls.

Tracks work as layers: clips on the SAME track are sequential — if a new clip's range overlaps an existing clip on that track, the existing clip is trimmed/split/removed to make room, matching the UI's drag-onto-track overwrite behavior.

NESTING: mediaRef may also be a timelineId — the timeline is placed as a single live nested clip (mediaType 'sequence'), with a linked audio clip when the child has audio. Duration defaults to the child's full length; source and endFrame work as for video. Cycles (a timeline containing itself) and empty timelines are rejected.

**Required:** "entries"

---

## `insert_clips`

Inserts one or more media assets at a single point and RIPPLES: every clip at or after atFrame is pushed right to open a gap, so nothing is overwritten. This is the non-destructive counterpart to add_clips (which clears the landing region, trimming/splitting/removing whatever's there). Use insert_clips to splice footage in without losing existing clips; use add_clips to fill empty space or deliberately overwrite.

Entries are laid end-to-end starting at atFrame on the target track (entry[0] at atFrame, entry[1] immediately after, ...). The push equals the sum of the entries' durations and is applied to the target track, every sync-locked track, AND the audio track any auto-created linked audio lands on — so a clip and its linked audio stay aligned. As in add_clips, a video asset with audio spawns a linked audio clip. One undoable action; one bad entry rejects the whole call with no partial state.

trackIndex is required — ripple needs an existing track to push. For placement into empty space, use add_clips.

As in add_clips, mediaRef may be a timelineId to splice in a nested timeline.

**Required:** "trackIndex", "atFrame", "entries"

---

## `move_clips`

Moves one or more clips to a new track and/or frame position. Single undoable action. Each move specifies the clip ID and at least one of toTrack (must be compatible with the clip's media type) and toFrame. Overlap on the destination is resolved as in add_clips (existing clips on the destination track are trimmed/split/removed). Linked partners follow the named clip: startFrame propagates as a delta to preserve l-cut / j-cut offsets; tracks stay with the named clip. Multicam clips must move as a whole group; partial group moves and camera lane changes are refused.

**Required:** "moves"

---

## `remove_clips`

Removes one or more clips by ID as a single undoable action. Any clip that belongs to a link group (e.g. a video with its paired audio) takes its whole group with it, matching the UI's linked-delete behavior.

**Required:** "clipIds"

---

## `manage_clip_links`

Links or unlinks clips as one undoable action without moving, trimming, or aligning them. link merges the complete existing groups touched by clipIds and requires at least two clips of different media types. unlink accepts one or more members and dissolves each member's complete link group. Use unlink before independently trimming the audio or video side of a J-cut or L-cut; relink afterward when the clips should move together again.

**Required:** "action", "clipIds"

---

## `manage_tracks`

Reorders, names, configures, or removes tracks in one undoable action. Prefer stable trackId selectors; numeric indexes use the order at call time. Index 0 renders on top, and reorder destinations must stay within the track's video/audio zone. Arrays run reorder → set → remove. User-authored names are returned separately from generated V1/A1 labels. Returns receipts and the resulting track order. Tracks holding multicam clips can't be removed or sync-unlocked.

---

## `split_clips`

Splits clips into two at one or more cut points, all in a single undoable action. A split only inserts a boundary — it never trims media or moves clips, so unlike ripple_delete_ranges nothing shifts and there's no gap to close.

Two modes — pass exactly one:
• splits: an array of {clipId, atFrame} (project frames). Use when you know the clip IDs.
• trackIndex + frames: cut one track at the given project frames; each frame is matched to whichever clip on that track contains it. Pairs naturally with get_transcript / get_timeline project frames.

Every frame must fall strictly between a clip's start and end. Multiple cuts on the SAME clip are allowed — pass all the frames at once and each is resolved against the current sub-clips. Duplicate cut points are ignored. Linked audio/video partners are split at the same frame so A/V stays in sync, and the right halves are regrouped into their own link pair. One bad cut point rejects the whole call with no partial state.

**Params:** items

**Required:** "clipId", "atFrame"

---

## `ripple_delete_ranges`

Cuts one or more ranges out and closes the gaps in one undoable action — the fast path for filler-word/dead-air removal. Replaces hand-cranked split_clips → remove_clips → move_clips loops: pass every range at once.

Two modes — pass exactly one of clipId or trackIndex:
• trackIndex (preferred for transcript-driven cuts): ranges are PROJECT frames and may span any number of clips on that track. get_transcript returns a clips array with nested words in project frames — collect every cut across the whole timeline and pass them in ONE call, no per-clip splitting and no re-reading the timeline between cuts. units must be 'frames'.
• clipId: ranges are cut within that single clip only, clamped to its visible span. Allows units 'seconds' (source-media seconds, e.g. inspect_media WITHOUT a clipId or search_media hits); 'frames' = project frames. Use when you already have one clip's per-word timestamps.

Overlapping ranges merge. Linked audio/video partners of every touched clip are cut on the same span so A/V stays in sync. Remaining clips shift left to close every gap; sync-locked tracks shift along to preserve alignment (their content isn't cut). Refuses without changing anything if a sync-locked track can't absorb the shift (e.g. it would move past frame 0). The refusal names the blocking track (e.g. "V2") — map it to its index via get_timeline and pass that index in ignoreSyncLockedTracks to cut anyway, leaving that track's clips in place. Returns the anchor track's post-cut layout (clip ids/frames) so you don't need to re-read.

**Required:** "ranges"

---

## `swap_clip_media`

Replace a clip's source with another library asset while preserving its edit, timing, framing, effects, and keyframes. Linked video/audio clips that share the source are updated together. The replacement must have the same source type, contain audio when a linked audio clip needs it, and cover the current source range; extra tail remains available for trimming. Text, nested timeline, and multicam clips are refused; use change_cam for multicam.

**Required:** "clipId", "mediaRef"

---

## `set_clip_properties`

Apply the same generic clip property values to one or more clips in a single undoable action. Pass any combination of durationFrames, trimStartFrame, trimEndFrame, speed, volumeDb, opacity, fades, edgeRounding, edgeSoftness, transform, crop, or blendMode (video/image clips only). For text content, typography, captions, and text animation, use update_text.

NOT for preview layout — split screen, picture-in-picture, grid, sidebar, and any multi-clip canvas arrangement belong to apply_layout, which sets transform and crop together. Do not use transform or crop here (or set_keyframes position/scale/crop) to build those layouts.

All values apply to every clip in clipIds; for per-clip differences, make separate calls. trimStartFrame/trimEndFrame are offsets from the source media, not the timeline. speed 1.0 is normal, <1.0 slows (clip gets longer on the timeline), >1.0 speeds up. volumeDb is −60 through +15 dB; 0 dB keeps source level and −60 dB is mute. opacity is 0.0–1.0. fadeInFrames/fadeOutFrames are clip-relative lengths; 0 clears that fade, and their sum must fit within the resulting clip duration. Fades multiply existing opacity or volume keyframes instead of replacing them: visual/text clips fade opacity, while audio clips fade gain. Fades are per-clip and don't propagate to linked media — include both the visual clip id and its nested audio.id from get_timeline to fade picture and sound together. edgeRounding and edgeSoftness are 0.0–1.0, where 1 reaches half the shorter visible edge. transform is for rare single-clip tweaks only — 0–1 normalized canvas coords, partial merge; rotation is clockwise degrees; flipHorizontal/flipVertical mirror across the axis.

For moves and start-frame changes, use move_clips. For animated values (keyframes), use set_keyframes — setting volumeDb, opacity, transform.rotation, or crop here clears any existing keyframe track on that property.

Timing changes (durationFrames, trimStartFrame, trimEndFrame, speed) on a linked clip carry over to its linked partner so audio/video stay in sync — same as the timeline UI. Per-clip fields (volumeDb, opacity, fades, edgeRounding, edgeSoftness, transform, crop, blendMode) don't propagate. trim and speed are skipped for text partners.

Timing fields (trims, durationFrames, speed) are refused on multicam clips — they would slip the clip out of sync; property fields stay editable, and angle changes go through change_cam.

**Required:** "clipIds"

---

## `copy_clip_settings`

Copy one clip's static settings to one or more clips of the same media type in a single undoable action. Use for requests such as "make these shots look like that one," "use this title style," or "give these audio clips the same treatment."

Choose exactly one target mode. targetClipIds applies to an explicit list and refuses any mismatched media type. targetTrack selects every same-type clip on one stable trackId; add range [startFrame, endFrame) to limit it to clips intersecting that part of the timeline. Track mode excludes the source and mismatched clips, returns compact matched/changed/unchanged/incompatible counts instead of clip IDs, and refuses when no compatible clips match.

Video, image, Lottie, and nested-timeline clips copy transform, crop, opacity, edge rounding/softness, blend mode, and the complete effect stack including color. Text clips copy typography/style, text animation, fill mode, position, rotation, flips, opacity, and effects; target text, word timings, and caption membership stay intact, and the box is refit to the target content. Audio clips copy volume and effects, including denoise. Settings absent from the source clear the corresponding target setting.

This does NOT copy placement, duration, trims, speed, fades, top-level keyframes, media, links, caption groups, or multicam membership. Use set_clip_properties and set_keyframes for temporal changes. Linked audio is a separate audio clip: copy it explicitly using the nested audio.id from get_timeline.

**Params:** targetTrack

**Required:** "trackId"

---

## `set_keyframes`

Set animated keyframes on one property of one clip. Replaces the existing keyframe track for that property (pass an empty array to clear). Frames are CLIP-RELATIVE offsets (0 = first frame of the clip), so keyframes follow the clip when it moves. Rows are sorted by frame internally and the LAST row for any duplicate frame wins. Values must be finite numbers. Each row is `[frame, ...values, interp?]` where interp ∈ {linear, hold, smooth} (default smooth).

Properties and their value layouts:
  • volumeDb `[frame, decibels]` — −60 through +15 dB; 0 dB keeps source level and −60 dB is mute
  • opacity `[frame, value]` — value 0.0–1.0
  • rotation `[frame, degrees]` — clockwise degrees
  • position `[frame, topLeftX, topLeftY]` — TOP-LEFT corner in 0–1 normalized canvas coords. NOT the center. (Default static transform centers a full-canvas clip, so top-left of the static is (0, 0); a centered half-size clip has top-left (0.25, 0.25).)
  • scale `[frame, width, height]` — clip's normalized width and height in 0–1 canvas coords (1.0 = fills the canvas axis), NOT a scale factor. Text clips use this same track; use matching width/height ratios for uniform text scale.
  • crop `[frame, top, right, bottom, left]` — animated side insets in 0–1 of the source media. Constant crop belongs on set_clip_properties.
  • blur `[frame, radius]` — whole-layer Gaussian blur from 0–100 px; supported by visual clips including video, images, text, and nested timelines.

Motion keyframes (position/scale/rotation) override the static `transform` value when active.

**Required:** "clipId", "property", "keyframes"

---

## `apply_layout`

Arrange multiple clips into a common multi-video layout (split screen, picture-in-picture, grid) in one undoable action — the fast path for composing several videos in one frame. Use this instead of hand-setting transforms and screenshot-checking alignment with inspect_timeline.

You pick a named layout and assign a clip to each of its slots; the tool computes every transform and crop so each clip FILLS its region edge-to-edge WITHOUT stretching — the source is cropped to the slot's shape (cover), like a layout template the videos are dropped into. Pass fit='fit' to letterbox the whole source inside its slot instead (no crop, may leave bars) — use only when the full frame must stay visible (e.g. a screen recording).

The crop is centered by default. When that chops off something important (a face cropped at the forehead, a subject off to one side), bias which part survives: 'anchor' is a coarse shortcut ('top' keeps the top, etc.), while anchorX/anchorY (0–1) give continuous control for in-between framing — e.g. anchorY 0.35 moves the crop only slightly toward the top, not all the way. To nudge framing after the fact, call apply_layout again with adjusted anchorX/anchorY (clipIds mode re-crops in place).

Two modes (don't mix across slots):
• Place new clips: give each slot a 'mediaRef' (media asset from get_media, or a timelineId to nest) plus top-level startFrame (default 0) and endFrame. Creates one stacked video track per slot at that time range; for PIP the inset is placed on top automatically. Video and nested-timeline clips bring their linked audio.
• Re-layout existing clips: give each slot 'clipIds' — one or more existing clips (video, image, or nested timeline / mediaType 'sequence'), all framed into that slot (handy when a track holds several sequential takes). Only transforms/crop change — timing and tracks are untouched (so existing track order decides stacking).

Every slot of the chosen layout must be filled. Layouts and their slot names:
  • full — main
  • side_by_side — left, right
  • top_bottom — top, bottom
  • pip_bottom_right / pip_bottom_left / pip_top_right / pip_top_left — main, inset
  • grid_2x2 / grid_3x3 / grid_4x4 — equal cells named rNcN, counting from the TOP-LEFT: row 1 is the top row, column 1 is the left column. So r1c1 is top-left, a 3x3's middle is r2c2, and a 3x3's bottom-right is r3c3
  • main_sidebar — main (70%), sidebar (30%)
  • three_up — left, center, right (three vertical columns)
  • three_stack — top, middle, bottom (three horizontal rows)

**Params:** items

**Required:** "slot"

---

## `sync_clips`

Align one or more clips to a reference clip by shifting targets on the timeline — use for dual-system sound (camera + external audio) or multicam. Default mode 'auto' aligns by embedded source timecode when both files carry one (exact, confidence 1.0), falling back to audio cross-correlation otherwise (seeded by capture dates when present); force a method with mode. referenceClipId stays put unless a target would land before frame 0, in which case the whole group shifts right together (reported as shiftedFrames). Returns offsetFrames, confidence (0–1), and method (timecode|audio) per target; refuses weak audio matches. Refused on multicam clips — a group's members are already aligned by its sync maps (manage_multicam).

**Required:** "referenceClipId"

---

## `manage_multicam`

Create or ungroup a multicam group. create syncs session media into ordinary stamped timeline clips: one program video track, one audio track per mic, and angle switches through change_cam. Use member kind angle for scratch-camera audio, mic for program audio, and both for a camera whose audio should play. Pin offsetSeconds when correlation cannot align a member. ungroup strips stamps and leaves clips in place.

**Params:** create, items, ungroup

**Required:** "mediaRef", "kind"

---

## `change_cam`

Switch a multicam group's camera angle over timeline frame ranges, full-frame or in a multi-angle layout. Batched entries are one undo step. Ranges where an angle was not recording clamp or skip. Returns switched count, optional clamps/skips/overlayClipIds, and program rows over the touched span.

Each entry is EITHER {range, angle} — full-frame switch — or {range, layout, angles} — PiP/split/grid: angles fill the layout's slots in order (first = the full-frame program slot; fewer angles than slots leaves cells empty), extra angles land as synced overlay clips above the program. A later full-frame entry over the same range clears the layout. Overlay clips are ordinary group clips — restyle with set_clip_properties/apply_layout, remove with remove_clips.

**Params:** items

**Required:** "range"

---

## `get_multicam`

Read a multicam group: members (angleLabel, kind, offsetSeconds, confidence, which is master), the current program cut as run-length [angle, startFrame, endFrame) rows in timeline frames, and the track indexes the group occupies. Use it to learn angle labels before change_cam, or to review the cut as one program instead of piecing it together from get_timeline's clips. Window long timelines with startFrame/endFrame.

---

## `undo`

Reverts the latest action from the editor's shared undo history, whether the user or agent made it. Call only when that latest action should be reversed. For example, verify a cut with get_transcript, then undo if it overshot and retry with corrected ranges. After undoing, ids and frames returned by the reverted action may be invalid; re-read with get_timeline or get_transcript before editing again. Takes no arguments.

---

## `get_transcript`

Returns the spoken transcript of the CURRENT timeline in project frames — the post-edit caption track in one call. Unlike inspect_media (which transcribes one source asset in isolation, in source seconds), this walks every audio/video clip on the timeline, maps each word through that clip's trim/speed/position, and concatenates in timeline order. Deleted ranges are gone by construction, so after cuts this always reflects what's actually audible — no stale results, no per-clip frame math. Pass trackIndex to isolate one dialogue or multicam mic track; omit it to read every eligible timeline source (multicam defaults to its master mic). The app chooses cloud only when the signed-in account has enough credits for the uncached request; otherwise it uses local transcription and reports the resolved transcriptionSource in the response.

Returns clips in timeline order, each with its words as compact [index, text, startFrame] rows (a word runs to the next word's start; the last word to its clip's end). Speakers, when identified, arrive as run-length turns: speakers = [[firstWordIndex, name], ...]. The index is a stable, 0-based position in the requested transcript; pass it straight to remove_words to cut that word (the intuitive path for text-based editing). Indices stay stable when paged with a window. Capped at 10000 words; page with startFrame/endFrame using nextStartFrame.

For comprehension rather than cutting — summarizing, finding a topic, take selection on long media — pass granularity='segments': sentence rows [firstWordIndex, text, start, end] at a fraction of the tokens, whose firstWordIndex jumps you back into word mode for the cut window.

Use for transcript-driven edits (filler-word / dead-air removal, locating a quote, take selection) and to verify what remains after cutting. To cut, prefer remove_words (give it the indices); drop to ripple_delete_ranges only for non-word-aligned spans.

---

## `remove_words`

Cut speech by the word, Descript-style — the primary tool for text-based editing (filler words, flubbed sentences, dropped retakes, tightening a ramble). Pass words for precise get_transcript indices/ranges, or matches for exact filler tokens like "um" and "uh". This resolves them to frames, removes the surrounding pause so survivors don't end up double-spaced, merges adjacent removals, cuts linked A/V partners, and closes the gaps. You never deal in frame numbers — that's the whole point versus ripple_delete_ranges.

Workflow: call get_transcript, read it as prose, then pass the indices of the words to drop. Omit language by default; remove_words reuses the previous get_transcript provider and track selection so word indices stay aligned. Words across multiple clips on ONE track are handled in a single undoable action, and any linked A/V partner (e.g. the video paired with this audio) is cut automatically. Edit one track at a time: if your indices span multiple unlinked tracks (e.g. two separate mics), the call is refused — cut each track in its own call, or link the tracks into one unit first. After it runs, indices have shifted — re-read get_transcript before another remove_words.

When to use which: words for selective edits after reading the transcript; matches for removing every exact filler token; ripple_delete_ranges only for spans that aren't word-aligned. Verify reworded retakes and sub-frame seam fragments against the word list, not a summary.

---

## `remove_silence`

Remove dead air — quiet, speech-free sections — from the timeline's audio, ripple-closing the gaps. Sections come from on-device speech detection (the same spans marked red on waveforms): non-speech runs whose level sits well below the recording's own speech level, so music beds and loud ambience are never cut. Cuts linked A/V partners and honors sync lock; the whole pass is one undoable action.

Omit clipIds to process the whole timeline, or pass clip IDs from get_timeline to process only those clips. A scoped selection must include audio from exactly one track, and all selected clips must share one track or belong to one linked A/V unit. By default, this uses the current Minimum Pause and Speech Padding controls. Pass either optional value as a one-shot override without changing those controls. Use this to tighten pacing (long pauses, dead space between takes) before or instead of word-level edits: remove_silence handles pauses, remove_words handles fillers and flubbed lines. No transcript needed. If it reports no dead air, speech analysis may still be running in the background — wait a moment and retry.

---

## `detect_beats`

Detect musical beats and downbeats in a media asset's audio, on-device. Returns beats and downbeats in SOURCE seconds (multiply by fps for frame values, same convention as search_media hits) plus estimated bpm. Downbeats mark bar starts — cut on downbeats for edits that land musically; beats are fine for faster montage rhythms.

Use for beat-synced editing: snapping cuts to a music bed, building montages where clip boundaries hit the beat, or timing text/caption entrances to the bar. To place a cut at a beat B on a clip, the timeline frame is startFrame + (B × fps − trimStartFrame) / speed. Works on music; speech or ambience returns few or no beats. Runs locally — no subscription needed.

**Required:** "mediaRef"

---

## `add_texts`

Adds text clips as timeline layers. Omit trackIndex on every entry to create one new top video track; otherwise set trackIndex on every entry. Text boxes auto-fit their content; transform optionally sets their alignment-relative horizontal anchor, vertical center, Z rotation, and static X/Y perspective tilt. Left-aligned text grows rightward from x, centered text grows around x, and right-aligned text grows leftward from x. Use style widthScale and heightScale to stretch glyphs. Use the nested style object for typography, outline, shadow, background, and whole-layer Gaussian blur. fillMode 'footage' stencils layers below through the letter shapes over a matte set by style.color; it defaults to black when color is omitted. 'inverted' renders white glyphs with Difference blending and ignores color, outline, shadow, and background while active. Use add_captions for spoken audio captions. Unknown fields are rejected.

**Required:** "entries"

---

## `update_text`

Updates text clips or a captionGroupId. The nested style object is a partial patch: omitted values stay unchanged. Use it for typography, color, outline, shadow, background, and whole-layer Gaussian blur. style.blur is measured in 1080p canvas pixels, scales with output resolution, and 0 removes it; setting it clears blur keyframes. Use style widthScale and heightScale to stretch glyphs. fillMode 'footage' stencils layers below through the glyphs over a matte set by style.color; entering it defaults to black when color is omitted. 'inverted' renders white glyphs with Difference blending and ignores color, outline, shadow, and background while active. Content and layout-affecting style changes auto-fit the box while preserving its alignment-relative x anchor. transform can reposition, rotate, or apply static X/Y perspective tilt without changing its size. Static Z rotation uses clockwise degrees and clears rotation keyframes. Unknown fields are rejected.

---

## `add_captions`

Transcribes spoken audio and creates caption text clips on their own track. Style, animation, and transform are optional overrides: omit them ALL for the app's clean default captions (plain white Helvetica, lower-third) — do not invent fonts, colors, outlines, backgrounds, blur, or animations the user didn't ask for. style.blur adds whole-layer Gaussian blur. Pass trackIndex to caption one dialogue or multicam mic track; omit it to automatically choose the timeline track with the most speech. The app uses cloud only when the signed-in account has enough credits for the uncached request; otherwise it uses local transcription. Cloud auto-detects language. Per-word animations are timed from the transcript. Alternatively, pass subtitleMediaRef (a subtitle asset from import_media/get_media) to place that SRT/WebVTT file's cues as captions at their authored timecodes — no transcription; the file's text, timing, and default styling are used as-is (overlapping cues are trimmed so clips never overlap on the track), so subtitleMediaRef can't be combined with any other parameter. Returns the caption group summary (captionGroupId, clipCount, frameRange, shared style, textPreview) — restyle it later with update_text and that captionGroupId.

---

## `apply_color`

Author/refine a color grade on video/image clips with named controls — the colorist path, distinct from apply_effect (looks/FX). Returns the clips with their resulting grade as a `color` object — the same object get_timeline shows; pass one back via the `color` parameter to copy a grade between clips (replaces the whole grade). MERGES with the clip's current grade: only the params you pass change, the rest are preserved, so you can nudge one knob at a time (pass reset:true to start from neutral). Applies as live, editable color.* effects; non-color effects untouched. Iterate: apply_color → inspect_color(clipId, reference) → read the gap → adjust → repeat. Undoable. All knobs optional. Color WHEELS use HUE (0–360°, standard) + AMOUNT per tonal zone — to push shadows teal, set shadowsHue 180 and shadowsAmount ~0.15. CURVES (master + per-channel R/G/B) give precise tone shaping — per-channel curves are tone-selective (e.g. pull the blue curve down in the highlights to tame a bright sky). HUE CURVES do secondary/qualified correction — target a source hue and shift its hue/saturation/lightness (e.g. desaturate greens, warm the skin) without a mask; pair with inspect_color's hueHistogram to find which hues are present. LUT applies a .cube film-look pack on top of the grade.

**Params:** items

**Required:** "targetHue"

---

## `apply_effect`

""
            Apply non-color effects (blur, sharpen, stylize, detail, key) to video/image clips as a live,             editable effect stack — the looks/FX path, distinct from apply_color (grading). MERGES: each effect             you pass is added or updated by type; effects you don't mention are left in place. Pass enabled:false             to bypass one without removing it, or list its type in `remove` to delete it. Out-of-range params are             clamped; params you omit keep their current (or default) value. Effects render in a fixed canonical             order regardless of the order you pass them. Setting blur.gaussian.radius clears blur keyframes.             Undoable. Returns the clips with their resulting             effects as [{type, params}] — the same shape this tool accepts, so copying effects between clips             is passing a clip's effects array back in.

            Available effects — type: param (range, default):
            \(Self.effectCatalog())
            ""

**Params:** items

**Required:** "type"

---

## `inspect_color`

Measure color scopes of a timeline clip's current graded look (clipId) OR a raw media asset (mediaRef) — black/white points, % clipping, mean & per-channel levels, shadow/mid/highlight color tilt, saturation, warm-cool / green-magenta balance, and a saturation-weighted hueHistogram (12 bins of 30° from 0°/red — shows which hues are present, e.g. an orange cluster = skin, a cyan/blue cluster = sky) — and return the rendered frame too. Use this to grade by the numbers instead of eyeballing, to find hues to target with apply_color's hueCurves, or to measure footage/references before grading. clipId applies the clip's effects (graded look); mediaRef measures the raw asset. Pass a reference image/video id to also measure it and get the subject−reference GAP plus hints that map onto apply_color knobs. The loop: apply_color → inspect_color(clipId, reference) → read the gap → adjust → repeat until the gap is small.

---

## `denoise_audio`

Remove background noise from audio clips using an on-device speech-enhancement model (DeepFilterNet3). strength is a dry/wet mix 0-1: 0 leaves the audio untouched, 1 is fully denoised. Full strength can sound thin or over-gated on real-world recordings, so the default is 0.6. The bake runs in the background — the timeline updates automatically when it finishes; no need to poll. Pass enabled:false to turn denoise off. Undoable.

**Required:** "clipIds"

---

## `list_models`

Lists AI models with their capabilities (durations, aspect ratios, resolutions, draft mode, first/last frame support, reference support, voices/category for audio, and configurable settings for upscalers). Always call before generate_video, generate_image, generate_audio, or upscale_media so the model you pick actually supports the constraints you need. Returns { models, loaded } — if loaded=false the catalog hasn't synced yet (e.g. user not signed in); the models array may be empty even when models exist, so do not conclude no models are available. Retry after the user signs in.

---

## `generate_video`

Starts an async AI video generation. Returns a placeholder asset ID immediately; generation runs in the background and the asset becomes usable in add_clips once ready. Costs real money and is not undoable.

---

## `generate_image`

Starts an async AI image generation. Returns a placeholder asset ID immediately; generation runs in the background. Costs real money and is not undoable.

**Required:** "prompt"

---

## `generate_audio`

Starts an async AI audio generation or transformation. Returns a placeholder asset ID immediately; the asset appears in get_media and becomes usable in add_clips once ready. TTS converts text into speech. Generative audio models create dialogue, music, or sound effects from a prompt, video, or supported image/audio references. Voice Cleanup isolates speech from background audio. Dubbing translates source speech while preserving speaker delivery; pass targetLanguage. For models whose inputs include audio or video, provide sourceMediaRef. Video-to-audio scoring models also accept videoSourceStartFrame+videoSourceEndFrame and place the result on the timeline automatically. Other results land in the media library for placement with add_clips. Use list_models with type='audio' to inspect inputs, category, voices, reference caps, and limits. Costs real money and is not undoable.

---

## `upscale_media`

Enhances an existing video or image with an AI upscaler. It can change resolution, interpolate video frame rate, or apply model-specific restoration settings. Returns a placeholder asset ID immediately; the result appears in get_media once ready. Call list_models with type='upscale' first and use its exact setting IDs and values. Costs real money and is not undoable.

**Required:** "mediaRef"

---

## `send_feedback`

Report an agent limitation or bug to the Palmier team so they can improve the product. Use when you can't do what the user asked because a capability or tool is missing or behaves wrong, the result is clearly off, or the user is plainly hitting a rough edge. This sends directly — there is no user confirmation step — so write the report in English and PARAPHRASE in your own words: translate non-English user text to English, and never include verbatim user messages, prompts, file paths, media, transcript text, or any project content. App/OS version and your recent tool names are attached automatically. Use sparingly: at most once per distinct issue.

**Required:** "category", "summary"

---

