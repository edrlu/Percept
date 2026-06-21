import { NextResponse } from "next/server";
import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { appendJobLog, extractFrame, jobDir, probe, readJob, readJobLogTail, sourceDir, writeJob, type RegenJob } from "@/app/lib/regen";
import { REGEN_META_PROMPT } from "@/app/lib/regenPrompt";

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
      await extractFrame(source, Math.max(0, startSec), path.join(dir, `${id}_start.png`));
      await extractFrame(source, Math.max(0, Math.min(Math.max(0, meta.duration - 0.05), endSec)), path.join(dir, `${id}_end.png`));
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
    await copyFile(path.join(frames, `${frameId}_start.png`), path.join(dir, "frame_start.png"));
    await copyFile(path.join(frames, `${frameId}_end.png`), path.join(dir, "frame_end.png"));

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
      metaPrompt: REGEN_META_PROMPT,
      job,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Frame extraction failed";
    await writeJob({ id, status: "error", stage: "setup", startSec, endSec, durationSec, totalSec: 0, sourceId, frameId, error: message, createdAt: new Date().toISOString() });
    await appendJobLog(id, `setup FAILED: ${message}`);
    return NextResponse.json({ error: message, logTail: await readJobLogTail(id) }, { status: 500 });
  }
}
