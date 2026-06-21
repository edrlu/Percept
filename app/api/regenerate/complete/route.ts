import { NextResponse } from "next/server";
import { copyFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { appendJobLog, dataDir, extractAudioMp3, jobDir, mergeReplace, readJob, readJobLogTail, scoreArchivedTakeMp3, sourceDir, writeJob } from "@/app/lib/regen";

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
  const bytes = Buffer.from(await clip.arrayBuffer());
  await writeFile(clipPath, bytes);
  console.log(`[regen-api ${new Date().toISOString()}] /complete ${jobId} · received clip ${bytes.length}B · merging [${job.startSec.toFixed(2)}s, ${job.endSec.toFixed(2)}s]`);
  await appendJobLog(jobId, `/complete received clip=${bytes.length}B; merging replace=[${job.startSec.toFixed(3)}, ${job.endSec.toFixed(3)}]`);

  // Archive this take's generated clip and audio alongside the run's floor clip.
  // The filler scorer consumes the saved MP3 and writes a temporary 50–100 grade
  // to the job after the regeneration has successfully merged.
  let archivedMp3: string | undefined;
  if (job.runId) {
    try {
      const ddir = dataDir(job.runId);
      await mkdir(ddir, { recursive: true });
      const take = `take_${(job.takeIndex ?? 0) + 1}`;
      await copyFile(clipPath, path.join(ddir, `${take}.mp4`));
      archivedMp3 = path.join(ddir, `${take}.mp3`);
      await extractAudioMp3(clipPath, archivedMp3, { jobId });
      await appendJobLog(jobId, `archived generated take → data/${job.runId}/${take}.{mp4,mp3}`);
    } catch (err) {
      archivedMp3 = undefined;
      await appendJobLog(jobId, `WARN: could not archive take to data/${job.runId}: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  await writeJob({ ...job, status: "merging", stage: "merge" });
  try {
    const t = Date.now();
    await mergeReplace(source, clipPath, job.startSec, job.endSec, final, { jobId });
    let score: number | undefined;
    if (archivedMp3) {
      try {
        await appendJobLog(jobId, "filler scorer started from archived MP3");
        score = await scoreArchivedTakeMp3(archivedMp3);
        await appendJobLog(jobId, `filler scorer complete · grade=${score}/100`);
      } catch (err) {
        await appendJobLog(jobId, `WARN: filler scorer failed: ${err instanceof Error ? err.message : String(err)}`);
      }
    }
    await writeJob({ ...job, status: "done", stage: "complete", score });
    await appendJobLog(jobId, `/complete merge OK in ${((Date.now() - t) / 1000).toFixed(1)}s; final=${final}`);
    console.log(`[regen-api ${new Date().toISOString()}] /complete ${jobId} · merge OK in ${((Date.now() - t) / 1000).toFixed(1)}s → done`);
    return NextResponse.json({ ok: true, score, downloadUrl: `/api/regenerate/file?job=${jobId}&name=final.mp4` });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Merge failed";
    await writeJob({ ...job, status: "error", stage: "merge", error: message });
    await appendJobLog(jobId, `/complete MERGE FAILED: ${message}`);
    console.error(`[regen-api ${new Date().toISOString()}] /complete ${jobId} · MERGE FAILED: ${message}`);
    return NextResponse.json({ error: message, logTail: await readJobLogTail(jobId) }, { status: 500 });
  }
}
