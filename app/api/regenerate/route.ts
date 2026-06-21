import { NextResponse } from "next/server";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { extractFrame, jobDir, probe, readJob, writeJob, type RegenJob } from "@/app/lib/regen";
import { REGEN_META_PROMPT } from "@/app/lib/regenPrompt";

export const runtime = "nodejs";
export const maxDuration = 120;

/** GET /api/regenerate?job=<id> — poll a job's status. */
export async function GET(request: Request) {
  const id = new URL(request.url).searchParams.get("job");
  if (!id) return NextResponse.json({ error: "Missing job id" }, { status: 400 });
  const job = await readJob(id);
  if (!job) return NextResponse.json({ error: "Unknown job" }, { status: 404 });
  return NextResponse.json(job, { headers: { "cache-control": "no-store" } });
}

/**
 * POST /api/regenerate — create a regeneration job for one cut.
 * formData: video (File), startSec, endSec, durationSec (5|10), label?
 * Saves the source, extracts the cut's start/end frames, and queues the job for
 * the agent to generate (Claude prompt → Pika generate_video → /complete).
 */
export async function POST(request: Request) {
  let form: FormData;
  try { form = await request.formData(); }
  catch { return NextResponse.json({ error: "Expected multipart form data" }, { status: 400 }); }

  const video = form.get("video");
  const startSec = Number(form.get("startSec"));
  const endSec = Number(form.get("endSec"));
  const durationSec = Number(form.get("durationSec"));
  const label = (form.get("label") as string) || undefined;
  if (!(video instanceof File)) return NextResponse.json({ error: "Missing video" }, { status: 400 });
  if (!Number.isFinite(startSec) || !Number.isFinite(endSec) || endSec <= startSec) {
    return NextResponse.json({ error: "Invalid start/end" }, { status: 400 });
  }

  const id = `job_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const dir = jobDir(id);
  await mkdir(dir, { recursive: true });
  const source = path.join(dir, "source.mp4");
  await writeFile(source, Buffer.from(await video.arrayBuffer()));

  try {
    const meta = await probe(source);
    const total = meta.duration || endSec;
    await extractFrame(source, Math.max(0, startSec), path.join(dir, "frame_start.png"));
    await extractFrame(source, Math.min(total - 0.05, endSec), path.join(dir, "frame_end.png"));

    const job: RegenJob = {
      id,
      status: "awaiting_generation",
      startSec,
      endSec,
      durationSec: durationSec === 10 ? 10 : 5,
      totalSec: total,
      label,
      createdAt: new Date().toISOString(),
    };
    await writeJob(job);

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
    await writeJob({ id, status: "error", startSec, endSec, durationSec, totalSec: 0, error: message, createdAt: new Date().toISOString() });
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
