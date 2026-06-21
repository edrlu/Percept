# Clip regeneration pipeline

Cut a 5s or 10s slot out of the video, regenerate it with AI, and merge the new
clip back into the exact same slot so total length is unchanged.

Generation runs through the **Pika MCP** (Seedance 2.0, image→video) driven by
**Claude** — not an HTTP API call. MCP tools are only reachable by the agent, so this is
deliberately *agent-in-the-loop*: the web app does the deterministic media work
(frame extraction + merge) and hands the creative step to Claude.

That agent step is **automated** by `worker/regen-worker.mjs`, which `run.sh`
starts alongside the app. It watches `regen/<id>/job.json` for queued jobs and
spawns a headless `claude` (which already has the Pika MCP connected) to run the
generation + `/complete` handoff — so a Regenerate click now completes on its
own. The steps below describe what that agent does; you only run them by hand if
the worker isn't running (e.g. `claude` isn't installed).

## Flow

```
[browser]  draw a cut (snaps to 5s/10s)  →  click Regenerate
   │  POST /api/regenerate  (video + startSec/endSec/durationSec)
   ▼
[next]     save source.mp4, ffmpeg-extract frame_start.png + frame_end.png
           write job.json { status: "awaiting_generation", ... }     →  regen/<id>/
   │
   ▼
[claude]   (agent, via MCP — auto-run by worker/regen-worker.mjs)
           1. read regen/<id>/frame_start.png + frame_end.png
           2. run the prompt-engineer meta-prompt (app/lib/regenPrompt.ts) on the
              two frames → produce `prompt` (Seedance takes no negative_prompt)
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

The browser polls every 2.5s while a job is `awaiting_generation` / `merging`,
so the card updates on its own once the agent finishes step 4.

## Agent step — generate_video parameters

For a queued job (scan `regen/*/job.json` for `status: "awaiting_generation"`):

```jsonc
mcp__pika__generate_video({
  provider: "seedance",
  mode:     "image_to_video",
  image:        <frame_start.png bytes>,   // START frame
  end_image:    <frame_end.png bytes>,     // END frame (Seedance morph target — NOT image_tail)
  prompt:       "<from the meta-prompt>",
  duration:     job.durationSec,           // 5 or 10
  resolution:   "1080p",                   // Seedance uses resolution, not quality_mode
  aspect_ratio: "16:9",                    // match the frames' orientation
  sound:        false
})
// Seedance rejects negative_prompt / quality_mode / prompt_adherence / image_tail.
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

- `worker/regen-worker.mjs` — poller that auto-runs the agent step (headless `claude`).
- `app/lib/regen.ts` — ffprobe/extractFrame/mergeReplace (local ffmpeg).
- `app/lib/regenPrompt.ts` — the prompt-engineer meta-prompt (step 1).
- `app/api/regenerate/route.ts` — create job + extract frames; GET poll.
- `app/api/regenerate/complete/route.ts` — receive clip, merge, mark done.
- `app/api/regenerate/file/route.ts` — serve frames / download final.
- `regen/<id>/` — per-job scratch (gitignored): source, frames, clip, final, job.json.

Requires `ffmpeg`/`ffprobe` on PATH. No TRIBE v2 worker needed for regeneration.
