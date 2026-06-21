import { NextResponse } from "next/server";
import { copyFile, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { appendJobLog, dataDir, extractFramePair, extractSegment, fileExists, FRAME_TAIL_SAFETY_SECONDS, jobDir, probe, readJob, readJobLogTail, sourceDir, writeJob, type RegenJob } from "@/app/lib/regen";

export const runtime = "nodejs";
export const maxDuration = 120;

const rlog = (msg: string) => console.log(`[regen-api ${new Date().toISOString()}] ${msg}`);

/** GET /api/regenerate?job=<id> — poll a job's status. */
export async function GET(request: Request) {
  const id = new URL(request.url).searchParams.get("job");
  if (!id) return NextResponse.json({ error: "Missing job id" }, { status: 400 });
  const job = await readJob(id);
  if (!job) return NextResponse.json({ error: "Unknown job" }, { status: 404 });
  return NextResponse.json({ ...job, logTail: await readJobLogTail(id), logUrl: `/api/regenerate/file?job=${id}&name=job.log` }, { headers: { "cache-control": "no-store" } });
}

/**
 * POST /api/regenerate — asset pipeline actions:
 * source stores a video once; frames prepares a splice's endpoints once; job
 * queues generation using those stored assets only.
 */
export async function POST(request: Request) {
  let form: FormData;
  try { form = await request.formData(); }
  catch { return NextResponse.json({ error: "Expected multipart form data" }, { status: 400 }); }

  const action = form.get("action") as string;
  const video = form.get("video");
  const startSec = Number(form.get("startSec"));
  const endSec = Number(form.get("endSec"));
  const durationSec = Number(form.get("durationSec"));
  const label = (form.get("label") as string) || undefined;
  if (action === "source") {
    if (!(video instanceof File)) return NextResponse.json({ error: "Missing video" }, { status: 400 });
    const id = `source_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const dir = sourceDir(id);
    await mkdir(path.join(dir, "frames"), { recursive: true });
    const source = path.join(dir, "source.mp4");
    try {
      await writeFile(source, Buffer.from(await video.arrayBuffer()));
      const meta = await probe(source);
      rlog(`source stored ${id} · ${video.size}B · duration=${meta.duration}s`);
      return NextResponse.json({ sourceId: id, duration: meta.duration });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Source video could not be stored or probed";
      rlog(`source FAILED ${id}: ${message}`);
      return NextResponse.json({ error: message }, { status: 500 });
    }
  }

  const sourceId = form.get("sourceId") as string;
  if (!sourceId) return NextResponse.json({ error: "Missing source asset" }, { status: 400 });
  const source = path.join(sourceDir(sourceId), "source.mp4");
  try { await readFile(source); } catch { return NextResponse.json({ error: "Source asset expired" }, { status: 404 }); }
  if (!Number.isFinite(startSec) || !Number.isFinite(endSec) || endSec <= startSec) return NextResponse.json({ error: "Invalid start/end" }, { status: 400 });

  if (action === "frames") {
    const id = `frames_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const dir = path.join(sourceDir(sourceId), "frames");
    try {
      const meta = await probe(source);
      const start = Math.max(0, startSec);
      const lastDecodableTime = Math.max(0, meta.duration - FRAME_TAIL_SAFETY_SECONDS);
      const end = Math.max(0, Math.min(lastDecodableTime, endSec));
      if (end <= start) {
        return NextResponse.json({ error: "This splice reaches beyond the last decodable video frame. Move it left and try again." }, { status: 422 });
      }
      await extractFramePair(source, start, end, dir, id);
      rlog(`frames ${id} extracted from ${sourceId} · start=${startSec.toFixed(2)}s end=${endSec.toFixed(2)}s`);
      return NextResponse.json({ frameId: id, startFrame: `/api/regenerate/file?source=${sourceId}&frame=${id}&edge=start`, endFrame: `/api/regenerate/file?source=${sourceId}&frame=${id}&edge=end` });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Frame extraction failed";
      rlog(`frames FAILED ${id} from ${sourceId}: ${message}`);
      return NextResponse.json({ error: message }, { status: 500 });
    }
  }

  const frameId = form.get("frameId") as string;
  if (!frameId) return NextResponse.json({ error: "Missing prepared frames" }, { status: 400 });
  const provider: RegenJob["provider"] = form.get("provider") === "kling" ? "kling" : "seedance";
  const agent: RegenJob["agent"] = form.get("agent") === "claude" ? "claude" : "codex";
  // A regeneration run is a batch of takes on one segment; the client stamps every
  // take with a shared runId so they archive into the same data/<runId>/ folder.
  const runId = (form.get("runId") as string) || undefined;
  const takeIndexRaw = Number(form.get("takeIndex"));
  const takeIndex = Number.isInteger(takeIndexRaw) && takeIndexRaw >= 0 ? takeIndexRaw : undefined;

  // Fail fast: if the chosen agent's CLI isn't installed on the server, reject now
  // with a clear error instead of queuing a job the worker can never pick up (which
  // used to sit "awaiting_generation" forever). run.sh sets REGEN_AGENTS to the
  // installed CLIs (possibly ""); when it's unset (app started another way) we skip
  // the check rather than block.
  const declaredAgents = process.env.REGEN_AGENTS;
  if (declaredAgents !== undefined) {
    const available = declaredAgents.split(",").map((s) => s.trim()).filter(Boolean);
    if (!available.includes(agent)) {
      const detail = available.length
        ? `The '${agent}' agent CLI isn't installed on the server (available: ${available.join(", ")}). Pick an available agent in settings, or install '${agent}'.`
        : `No generation-agent CLI ('claude' or 'codex') is installed on the server, so clip regeneration can't run. Install one and restart.`;
      rlog(`job REJECTED · agent=${agent} unavailable (REGEN_AGENTS=${JSON.stringify(declaredAgents)})`);
      return NextResponse.json({ error: detail }, { status: 503 });
    }
  }

  const id = `job_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const dir = jobDir(id);
  await mkdir(dir, { recursive: true });
  try {
    const meta = await probe(source);
    const frames = path.join(sourceDir(sourceId), "frames");
    const startFrame = path.join(frames, `${frameId}_start.png`);
    const endFrame = path.join(frames, `${frameId}_end.png`);
    if (!(await fileExists(startFrame)) || !(await fileExists(endFrame))) {
      throw new Error("Splice boundaries are incomplete. Reposition or redraw the splice so Cerebra can prepare a new start/end frame pair.");
    }
    await Promise.all([
      copyFile(startFrame, path.join(dir, "frame_start.png")),
      copyFile(endFrame, path.join(dir, "frame_end.png")),
    ]);

    // Archive the floor clip (the original segment this run will splice out) once
    // per run. Every take shares the runId, so guard on the file existing and use
    // a temp+rename so the concurrent takes can't tear each other's write.
    if (runId) {
      const ddir = dataDir(runId);
      await mkdir(ddir, { recursive: true });
      const floor = path.join(ddir, "floor.mp4");
      if (!(await fileExists(floor))) {
        const tmp = path.join(ddir, `.floor.${process.pid}.${Math.random().toString(36).slice(2, 7)}.tmp.mp4`);
        await extractSegment(source, startSec, endSec, tmp, { jobId: id, label: "extract-floor" });
        await rename(tmp, floor);
        rlog(`floor clip archived → data/${runId}/floor.mp4 [${startSec.toFixed(2)}s, ${endSec.toFixed(2)}s]`);
      }
    }

    const job: RegenJob = {
      id,
      status: "awaiting_generation",
      stage: "queued",
      startSec,
      endSec,
      durationSec: durationSec === 10 ? 10 : 5,
      totalSec: meta.duration || endSec,
      sourceId,
      frameId,
      provider,
      agent,
      runId,
      takeIndex,
      label,
      createdAt: new Date().toISOString(),
    };
    await writeJob(job);
    await appendJobLog(id, `queued provider=${provider} agent=${agent} duration=${job.durationSec}s source=${sourceId} frame=${frameId} slot=${label ?? "?"}`);
    rlog(`job QUEUED ${id} · provider=${provider} agent=${agent} dur=${job.durationSec}s slot=${label ?? "?"} (agent '${agent}' available; worker should claim it shortly)`);

    return NextResponse.json({
      jobId: id,
      startFrame: `/api/regenerate/file?job=${id}&name=frame_start.png`,
      endFrame: `/api/regenerate/file?job=${id}&name=frame_end.png`,
      durationSec: job.durationSec,
      job,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Frame extraction failed";
    await writeJob({ id, status: "error", stage: "setup", startSec, endSec, durationSec, totalSec: 0, sourceId, frameId, error: message, createdAt: new Date().toISOString() });
    await appendJobLog(id, `setup FAILED: ${message}`);
    return NextResponse.json({ error: message, logTail: await readJobLogTail(id) }, { status: 500 });
  }
}
