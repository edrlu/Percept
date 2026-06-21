#!/usr/bin/env node
/**
 * Cerebra clip-regeneration worker.
 *
 * The regeneration pipeline (see REGENERATE.md) is deliberately
 * agent-in-the-loop: the Next backend extracts a cut's start/end frames and
 * queues a job, but the creative step — write a Seedance prompt, call Pika's
 * generate_video, hand the clip back to /api/regenerate/complete — only runs
 * through the Pika MCP, which is reachable by an agent and not by a plain HTTP
 * call. Previously that agent step had to be run by hand, so a queued job just
 * sat in `awaiting_generation` forever.
 *
 * This worker closes the loop: it watches regen/<id>/job.json for queued jobs
 * and, for each one, spawns a headless `codex exec` (which already has the Pika MCP
 * connected) to perform the generation + merge handoff. The job's status is
 * left as `awaiting_generation` while the agent works — a `.claimed` lock file
 * (not a status change) prevents double-processing — so the browser's existing
 * poll loop keeps polling until /complete flips it to merging/done.
 */
import { spawn } from "node:child_process";
import { readdir, readFile, writeFile, stat, access } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REGEN_DIR = process.env.CEREBRA_REGEN_DIR || path.join(ROOT, "regen");
const APP_URL = (process.env.CEREBRA_URL || `http://localhost:${process.env.PORT || 3000}`).replace(/\/$/, "");
const POLL_MS = Number(process.env.CEREBRA_REGEN_POLL_MS) || 4000;
const MAX_CONCURRENT = Number(process.env.CEREBRA_REGEN_CONCURRENCY) || 1;
// A claimed job whose agent never reported back is reclaimed after this long so
// a crashed agent doesn't strand the job permanently.
const STALE_CLAIM_MS = Number(process.env.CEREBRA_REGEN_STALE_MS) || 12 * 60_000;
const MODEL = process.env.CEREBRA_REGEN_MODEL || "";

const running = new Set();

function log(...args) {
  console.log(`[regen-worker ${new Date().toISOString()}]`, ...args);
}

async function exists(p) {
  try { await access(p); return true; } catch { return false; }
}

async function readJob(dir) {
  try { return JSON.parse(await readFile(path.join(dir, "job.json"), "utf8")); }
  catch { return null; }
}

async function writeJobStatus(dir, job, patch) {
  await writeFile(path.join(dir, "job.json"), JSON.stringify({ ...job, ...patch }, null, 2));
}

async function claimStale(dir) {
  // Returns true if a stale claim was cleared (so the job is processable again).
  const lock = path.join(dir, ".claimed");
  if (!(await exists(lock))) return false;
  try {
    const s = await stat(lock);
    if (Date.now() - s.mtimeMs > STALE_CLAIM_MS) return true;
  } catch { /* ignore */ }
  return false;
}

function buildPrompt(job, dir) {
  const provider = job.provider === "kling" ? "kling" : "seedance";
  const start = path.join(dir, "frame_start.png");
  const end = path.join(dir, "frame_end.png");
  // Each provider takes a different param set for the start->end transition.
  const step3 = provider === "kling"
    ? `3. Call mcp__pika__generate_video with: provider="kling", mode="image_to_video", image = the START frame (${start}), ` +
      `image_tail = the END frame (${end}), your prompt, your negative_prompt, duration=${job.durationSec}, ` +
      "quality_mode=\"pro\", prompt_adherence=\"strict\", sound=false. (Kling uses image_tail for the end frame and accepts negative_prompt / quality_mode / prompt_adherence.)"
    : `3. Call mcp__pika__generate_video with: provider="seedance", mode="image_to_video", image = the START frame (${start}), ` +
      `end_image = the END frame (${end}), your prompt, duration=${job.durationSec}, fast=true, resolution="720p", sound=false, ` +
      "and aspect_ratio matching the frames' orientation (\"16:9\" for landscape, \"9:16\" for portrait). " +
      "Do NOT pass negative_prompt, quality_mode, prompt_adherence, or image_tail — Seedance rejects those (image_tail is Kling-only; use end_image). " +
      "Note: fast tier requires 720p (1080p is rejected with fast).";
  return [
    "You are an automated worker finishing ONE clip-regeneration job for the Cerebra app.",
    "Work autonomously and do NOT ask questions — you have the permissions you need.",
    "",
    `Job id: ${job.id}`,
    `Job directory (absolute): ${dir}`,
    `START frame: ${path.join(dir, "frame_start.png")}`,
    `END frame:   ${path.join(dir, "frame_end.png")}`,
    `Clip length: ${job.durationSec} seconds`,
    `App base URL: ${APP_URL}`,
    "",
    `Model: ${provider === "kling" ? "Kling" : "Seedance"} (provider="${provider}")`,
    "",
    "Follow these steps exactly:",
    "1. Read both frame PNGs so you understand the shot (subjects, text/logos, lighting, what changes start->end).",
    "2. Read app/lib/regenPrompt.ts and apply its REGEN_META_PROMPT to those two frames to craft a `prompt` (and a `negative_prompt`) for an image_to_video shot from the START frame into the END frame. Favor one continuous, physically plausible action and a smooth eased camera/object trajectory; do not describe a morph unless the endpoints make that mechanism unavoidable.",
    step3,
    "Do NOT pass background=true — let the call block and return the video inline (fast/720p renders finish within the server budget). Inspect the tool schema to pass the frames in the form it expects (e.g. a media reference with base64 bytes, or upload_asset first). Only if it falls back to {task_id,status}: poll mcp__pika__task_status in a tight loop until completed/failed/cancelled, passing the task_id EXACTLY as returned — it is a long JWT; copy it verbatim, never truncate or edit it.",
    `4. Download the finished video to ${path.join(dir, "clip.mp4")} (use the tool's download or curl the returned URL via Bash).`,
    "5. Hand the clip back to the app so it merges in place and marks the job done:",
    `   curl -fsS -X POST ${APP_URL}/api/regenerate/complete -F "jobId=${job.id}" -F "clip=@${path.join(dir, "clip.mp4")};type=video/mp4"`,
    "6. Verify the response JSON has ok:true. If any step fails, print a line starting with \"REGEN_FAILED:\" and the reason.",
    "",
    "End with a single line: either \"REGEN_DONE\" or \"REGEN_FAILED: <reason>\".",
  ].join("\n");
}

function runAgent(job, dir) {
  return new Promise((resolve) => {
    const args = [
      "exec",
      "--cd", ROOT,
      "--dangerously-bypass-approvals-and-sandbox",
    ];
    if (MODEL) args.push("--model", MODEL);
    args.push(buildPrompt(job, dir));

    log(`job ${job.id}: launching generation agent`);
    const child = spawn("codex", args, { cwd: ROOT, stdio: ["ignore", "pipe", "pipe"], env: process.env });
    let tail = "";
    const onData = (d) => { tail = (tail + d.toString()).slice(-2000); };
    child.stdout.on("data", onData);
    child.stderr.on("data", onData);
    child.on("error", (err) => { log(`job ${job.id}: failed to spawn Codex:`, err.message); resolve({ ok: false, reason: err.message }); });
    child.on("exit", (code) => resolve({ ok: code === 0 && /REGEN_DONE/.test(tail), reason: tail.trim().split("\n").pop() || `exit ${code}` }));
  });
}

async function processJob(dir, job) {
  running.add(job.id);
  const lock = path.join(dir, ".claimed");
  try {
    await writeFile(lock, new Date().toISOString());
    const result = await runAgent(job, dir);

    // The agent reports back to /complete, which moves the job to merging/done.
    // Re-read so we never clobber that. Only intervene if it's still queued.
    const fresh = (await readJob(dir)) || job;
    if (fresh.status === "awaiting_generation") {
      if (result.ok) {
        log(`job ${job.id}: agent finished but job still queued — leaving for retry`);
      } else {
        log(`job ${job.id}: marking error — ${result.reason}`);
        await writeJobStatus(dir, fresh, { status: "error", error: result.reason || "Generation failed" });
      }
    } else {
      log(`job ${job.id}: ${fresh.status}`);
    }
  } catch (err) {
    log(`job ${job.id}: worker error:`, err.message);
  } finally {
    running.delete(job.id);
  }
}

async function tick() {
  if (running.size >= MAX_CONCURRENT) return;
  let entries;
  try { entries = await readdir(REGEN_DIR); } catch { return; }

  for (const name of entries) {
    if (running.size >= MAX_CONCURRENT) break;
    const dir = path.join(REGEN_DIR, name);
    if (running.has(name)) continue;

    const job = await readJob(dir);
    if (!job || job.status !== "awaiting_generation") continue;

    const claimed = await exists(path.join(dir, ".claimed"));
    if (claimed && !(await claimStale(dir))) continue; // someone owns it (or it's fresh)

    if (!(await exists(path.join(dir, "frame_start.png"))) || !(await exists(path.join(dir, "frame_end.png")))) continue;

    void processJob(dir, job);
  }
}

async function main() {
  log(`watching ${REGEN_DIR} → ${APP_URL} (concurrency ${MAX_CONCURRENT}, poll ${POLL_MS}ms)`);
  while (true) {
    await tick();
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
}

main().catch((err) => { log("fatal:", err); process.exit(1); });
