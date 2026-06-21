import { NextResponse } from "next/server";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { dataDir, fileExists } from "@/app/lib/regen";

export const runtime = "nodejs";
// Scoring runs up to 3 TRIBE inferences; a cold GPU can take minutes. Mirror the
// predict gateway's long budget so the UI waits instead of timing out.
export const maxDuration = 14400;

const VARIANT_COUNT = 3;

/**
 * POST /api/regenerate/score — score a run's 3 takes against the original.
 * Body: { runId, referenceId }. Reads data/<runId>/take_<n>.mp4, forwards them
 * to the worker /score_takes (which reuses the original's saved baseline), and
 * caches the result so reopening the picker is instant.
 */
export async function POST(request: Request) {
  const workerUrl = process.env.TRIBEV2_API_URL;
  if (!workerUrl) {
    return NextResponse.json(
      { error: "No TRIBE v2 worker configured. Set TRIBEV2_API_URL to enable scoring." },
      { status: 503 },
    );
  }

  let body: { runId?: string; referenceId?: string; takeIndex?: number };
  try { body = await request.json(); }
  catch { return NextResponse.json({ error: "Expected JSON { runId, referenceId, takeIndex? }" }, { status: 400 }); }
  const { runId, referenceId, takeIndex } = body;
  if (!runId) {
    return NextResponse.json({ error: "Missing runId" }, { status: 400 });
  }
  // referenceId is optional: without it (or without a saved baseline) the worker
  // scores each take within-video, so the model still runs and a score returns.
  // takeIndex (0-based) scores a SINGLE take, so the UI can score each take the
  // instant it finishes. Omit it to score the whole batch (cached for reopen).
  const single = typeof takeIndex === "number" && takeIndex >= 0;

  const ddir = dataDir(runId);
  const scoresFile = path.join(ddir, "scores.json");
  if (!single && await fileExists(scoresFile)) {
    try { return NextResponse.json(JSON.parse(await readFile(scoresFile, "utf8"))); }
    catch { /* unreadable cache: recompute */ }
  }

  const form = new FormData();
  form.append("referenceId", referenceId || "");
  let count = 0;
  const takeNums = single ? [takeIndex! + 1] : Array.from({ length: VARIANT_COUNT }, (_unused, i) => i + 1);
  for (const n of takeNums) {
    const p = path.join(ddir, `take_${n}.mp4`);
    if (!(await fileExists(p))) continue;
    const bytes = await readFile(p);
    form.append("takes", new Blob([bytes], { type: "video/mp4" }), `take_${n}.mp4`);
    count++;
  }
  if (count === 0) return NextResponse.json({ error: single ? `Take ${takeIndex! + 1} is not ready yet` : "No takes found for this run" }, { status: 404 });

  try {
    const upstream = await fetch(`${workerUrl.replace(/\/$/, "")}/score_takes`, {
      method: "POST",
      body: form,
      signal: AbortSignal.timeout(14_400_000),
    });
    const raw = await upstream.text();
    let data: unknown;
    try { data = JSON.parse(raw); } catch { data = { error: raw || "Scoring worker returned a non-JSON response" }; }
    if (!upstream.ok) return NextResponse.json(data, { status: upstream.status });
    if (!single) { try { await writeFile(scoresFile, JSON.stringify(data)); } catch { /* best-effort cache */ } }
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Scoring worker unavailable";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
