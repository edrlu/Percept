import { NextResponse } from "next/server";
import { writeFile } from "node:fs/promises";
import path from "node:path";
import { jobDir, mergeReplace, readJob, sourceDir, writeJob } from "@/app/lib/regen";

export const runtime = "nodejs";
export const maxDuration = 300;

/**
 * POST /api/regenerate/complete — hand the Pika-generated clip back to the
 * backend. The agent calls this after generate_video. formData: jobId, clip.
 * The backend merges the clip into the source in place and marks the job done.
 */
export async function POST(request: Request) {
  let form: FormData;
  try { form = await request.formData(); }
  catch { return NextResponse.json({ error: "Expected multipart form data" }, { status: 400 }); }

  const jobId = form.get("jobId") as string;
  const clip = form.get("clip");
  if (!jobId) return NextResponse.json({ error: "Missing jobId" }, { status: 400 });
  if (!(clip instanceof File)) return NextResponse.json({ error: "Missing clip" }, { status: 400 });

  const job = await readJob(jobId);
  if (!job) return NextResponse.json({ error: "Unknown job" }, { status: 404 });

  const dir = jobDir(jobId);
  const clipPath = path.join(dir, "clip.mp4");
  const source = path.join(sourceDir(job.sourceId), "source.mp4");
  const final = path.join(dir, "final.mp4");
  await writeFile(clipPath, Buffer.from(await clip.arrayBuffer()));

  await writeJob({ ...job, status: "merging" });
  try {
    await mergeReplace(source, clipPath, job.startSec, job.endSec, final);
    await writeJob({ ...job, status: "done" });
    return NextResponse.json({ ok: true, downloadUrl: `/api/regenerate/file?job=${jobId}&name=final.mp4` });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Merge failed";
    await writeJob({ ...job, status: "error", error: message });
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
