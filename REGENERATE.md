# Clip regeneration pipeline

Cut a 5s or 10s slot out of the video, regenerate it with AI, and merge the new
clip back into the exact same slot so total length is unchanged.

Generation runs through the **Pika MCP** (Seedance 2.0 or Kling, image→video —
selectable in the UI settings) driven by **Codex** — not an HTTP API call. MCP
tools are only reachable by the agent, so this is
deliberately *agent-in-the-loop*: the web app does the deterministic media work
(frame extraction + merge) and hands the creative step to Codex.

That agent step is **automated** by `worker/regen-worker.mjs`, which `run.sh`
starts alongside the app. It watches `regen/<id>/job.json` for queued jobs and
spawns a headless `codex exec` (which already has the Pika MCP connected) to run the
generation + `/complete` handoff — so a Regenerate click now completes on its
own. The steps below describe what that agent does; you only run them by hand if
the worker isn't running (e.g. `codex` isn't installed).

## Flow

```
[browser]  draw a cut (snaps to 5s/10s)  →  click Regenerate
   │  POST /api/regenerate  (video + startSec/endSec/durationSec)
   ▼
[next]     save source.mp4, ffmpeg-extract frame_start.png + frame_end.png
           write job.json { status: "awaiting_generation", ... }     →  regen/<id>/
           append regen/<id>/job.log with setup + ffmpeg trace
   │
   ▼
[codex]    (agent, via MCP — auto-run by worker/regen-worker.mjs)
           status → "generating"; append regen/<id>/agent.log
           1. read regen/<id>/frame_start.png + frame_end.png
           2. run this take's prompt-engineer meta-prompt (vidgenmd/take_<N>.md,
              chosen by job.takeIndex) on the two frames → produce `prompt` +
              `negative_prompt` (Kling uses both; Seedance ignores the negative_prompt)
           3. mcp__pika__generate_video (see params below) → download result → clip.mp4
           4. POST /api/regenerate/complete (jobId + clip.mp4)
   │
   ▼
[next]     mergeReplace(): [0,start] + clip + [end,total], re-encoded, audio-safe
           job.json status → "done"
   │
   ▼
[browser]  poll GET /api/regenerate?job=<id> flips the card to "Download"
           → GET /api/regenerate/file?job=<id>&name=final.mp4  (attachment)
```

The browser polls every 2.5s while a job is `awaiting_generation` /
`generating` / `merging`, so the card updates on its own once the agent
finishes step 4. Poll responses include `logTail` and `logUrl`; on disk,
`regen/<id>/job.log` has the server/ffmpeg trace and `regen/<id>/agent.log` has
the headless agent trace. If anything breaks, the UI links to the saved job log.

## Agent step — generate_video parameters

The generation model is chosen in the UI settings menu (APPEARANCE → GENERATION
MODEL) and stored on the job as `job.provider` (`"seedance"` default, or
`"kling"`). The two providers take **different params** for the start→end
transition, so the worker branches on `job.provider`:

For a queued job (scan `regen/*/job.json` for `status: "awaiting_generation"`):

```jsonc
// job.provider === "seedance"  (default)
mcp__pika__generate_video({
  provider: "seedance",
  mode:     "image_to_video",
  image:        <frame_start.png bytes>,   // START frame
  end_image:    <frame_end.png bytes>,     // END frame (Seedance morph target — NOT image_tail)
  prompt:       "<from the meta-prompt>",
  duration:     job.durationSec,           // 5 or 10
  fast:         true,                      // fast tier: ~20% cheaper, renders within the 260s inline budget
  resolution:   "720p",                    // fast tier requires 720p (1080p rejected with fast)
  aspect_ratio: "16:9",                    // match the frames' orientation
  sound:        false
})
// Seedance rejects negative_prompt / quality_mode / prompt_adherence / image_tail.
// fast+720p keeps the render under generate_video's ~260s server budget, so it
// returns inline — avoiding the fragile task_id (JWT) polling path.

// job.provider === "kling"
mcp__pika__generate_video({
  provider: "kling",
  mode:     "image_to_video",
  image:           <frame_start.png bytes>,  // START frame
  image_tail:      <frame_end.png bytes>,    // END frame (Kling morph target — NOT end_image)
  prompt:          "<from the meta-prompt>",
  negative_prompt: "<from the meta-prompt>", // Kling consumes the negative prompt
  duration:        job.durationSec,          // 5 or 10
  quality_mode:    "pro",
  prompt_adherence:"strict",
  sound:           false
})
```

Frames are ~250 KB PNGs — pass them inline as a media reference object
(`{ filename, mime_type, bytes_base64 }`). Then download the returned video to
`regen/<id>/clip.mp4` and:

```bash
curl -X POST http://localhost:3000/api/regenerate/complete \
  -F "jobId=<id>" -F "clip=@regen/<id>/clip.mp4;type=video/mp4"
```

## Why cuts snap to 5s / 10s

The clip is generated at the slot's exact length (Seedance accepts 4–15s; we use
5 or 10). Snapping the slot to that same length means the generated clip drops
back in 1:1 — `mergeReplace` swaps the slot for the clip and the overall runtime
stays put ("slot kept in place").

## Files

- `worker/regen-worker.mjs` — poller that auto-runs the agent step (headless `codex exec`).
- `app/lib/regen.ts` — ffprobe/extractFrame/mergeReplace (local ffmpeg).
- `vidgenmd/take_1.md`, `take_2.md`, `take_3.md` — the per-take prompt-engineer
  meta-prompts (step 1); the worker picks one by `job.takeIndex`.
- `app/api/regenerate/route.ts` — create job + extract frames; GET poll.
- `app/api/regenerate/complete/route.ts` — receive clip, merge, mark done.
- `app/api/regenerate/file/route.ts` — serve frames / download final.
- `regen/<id>/` — per-job scratch (gitignored): source, frames, clip, final, job.json, job.log, agent.log.

Requires `ffmpeg`/`ffprobe` on PATH. No TRIBE v2 worker needed for regeneration.
