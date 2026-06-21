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
 * connected) to perform the generation + merge handoff. The job is marked as
 * `generating` while the agent works and guarded by a `.claimed` lock file, so
 * the browser can show the active stage and stale claims can be recovered if the
 * agent process crashes.
 */
import { spawn } from "node:child_process";
import { readdir, readFile, writeFile, appendFile, stat, access } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REGEN_DIR = process.env.CEREBRA_REGEN_DIR || path.join(ROOT, "regen");
const APP_URL = (process.env.CEREBRA_URL || `http://localhost:${process.env.PORT || 3000}`).replace(/\/$/, "");
const POLL_MS = Number(process.env.CEREBRA_REGEN_POLL_MS) || 4000;
// Up to three clips regenerate at once: the UI queues a batch of cuts together
// and each spawns its own agent → Pika call, so they run as three concurrent
// generations rather than serially. Override with CEREBRA_REGEN_CONCURRENCY.
const MAX_CONCURRENT = Number(process.env.CEREBRA_REGEN_CONCURRENCY) || 3;
// A claimed job whose agent never reported back is reclaimed after this long so
// a crashed agent doesn't strand the job permanently.
const STALE_CLAIM_MS = Number(process.env.CEREBRA_REGEN_STALE_MS) || 12 * 60_000;
const MODEL = process.env.CEREBRA_REGEN_MODEL || "";
// Verbose by default; set CEREBRA_REGEN_DEBUG=0 to quiet the per-tick scan noise.
const DEBUG = process.env.CEREBRA_REGEN_DEBUG !== "0";

const running = new Set();

const ts = () => new Date().toISOString();
function log(...args) {
  console.log(`[regen-worker ${ts()}]`, ...args);
}

// Per-job debug logger: every line goes to the worker stdout (captured in
// .regen-worker-<port>.log) AND appended to regen/<id>/agent.log, so each job
// has a complete, self-contained trace of exactly what happened and when.
function makeJobLogger(dir, id, t0) {
  const file = path.join(dir, "agent.log");
  return (msg) => {
    const el = ((Date.now() - t0) / 1000).toFixed(1);
    console.log(`[regen-worker ${ts()}] [${id}] +${el}s ${msg}`);
    appendFile(file, `[${ts()} +${el}s] ${msg}\n`).catch(() => {});
  };
}

async function exists(p) {
  try { await access(p); return true; } catch { return false; }
}

async function readJob(dir) {
  try { return JSON.parse(await readFile(path.join(dir, "job.json"), "utf8")); }
  catch { return null; }
}

async function writeJobStatus(dir, job, patch) {
  await writeFile(path.join(dir, "job.json"), JSON.stringify({ ...job, ...patch, updatedAt: new Date().toISOString() }, null, 2));
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

function runAgent(job, dir, dlog) {
  return new Promise((resolve) => {
    // The UI chooses which MCP agent drives Pika. Both run the SAME prompt and
    // both already have the pika MCP connected; only the CLI invocation differs.
    const agent = job.agent === "claude" ? "claude" : "codex";
    const prompt = buildPrompt(job, dir);
    let cmd, args, summary;
    if (agent === "claude") {
      cmd = "claude";
      args = [
        "-p", prompt,
        "--add-dir", ROOT,
        "--allowedTools",
        "Read,Bash,mcp__pika__generate_video,mcp__pika__task_status,mcp__pika__upload_asset,mcp__pika__create_upload_return,mcp__pika__complete_upload_asset",
        "--dangerously-skip-permissions",
      ];
      if (MODEL) args.push("--model", MODEL);
      summary = `claude -p <prompt:${prompt.length}c> --add-dir ${ROOT} --allowedTools <Read,Bash,pika*> --dangerously-skip-permissions${MODEL ? ` --model ${MODEL}` : ""}`;
    } else {
      cmd = "codex";
      args = ["exec", "--cd", ROOT, "--dangerously-bypass-approvals-and-sandbox"];
      if (MODEL) args.push("--model", MODEL);
      args.push(prompt);
      summary = `codex exec --cd ${ROOT} --dangerously-bypass-approvals-and-sandbox <prompt:${prompt.length}c>${MODEL ? ` --model ${MODEL}` : ""}`;
    }

    // Non-global installs: run.sh sets REGEN_CLAUDE_CMD / REGEN_CODEX_CMD to the
    // resolved invocation (e.g. "npx --no-install claude"). Split on whitespace so
    // multi-token commands run correctly: cmd becomes "npx" and the leading tokens
    // are prepended to args.
    const baseCmd = (agent === "claude" ? process.env.REGEN_CLAUDE_CMD : process.env.REGEN_CODEX_CMD) || cmd;
    const baseParts = baseCmd.split(/\s+/).filter(Boolean);
    if (baseParts.length) {
      cmd = baseParts[0];
      if (baseParts.length > 1) args = [...baseParts.slice(1), ...args];
    }

    dlog(`SPAWN agent=${agent} via "${baseCmd}": ${summary}`);
    const t = Date.now();
    const child = spawn(cmd, args, { cwd: ROOT, stdio: ["ignore", "pipe", "pipe"], env: process.env });
    dlog(`agent pid=${child.pid}`);
    let full = "";
    const partial = { stdout: "", stderr: "" };
    const stream = (which) => (d) => {
      const s = d.toString();
      full = (full + s).slice(-40000);
      partial[which] += s;
      let i;
      while ((i = partial[which].indexOf("\n")) >= 0) {
        const line = partial[which].slice(0, i);
        partial[which] = partial[which].slice(i + 1);
        if (line.trim()) dlog(`${which}> ${line}`);
      }
    };
    child.stdout.on("data", stream("stdout"));
    child.stderr.on("data", stream("stderr"));
    child.on("error", (err) => { dlog(`SPAWN ERROR (${agent}): ${err.message}`); resolve({ ok: false, reason: `spawn failed: ${err.message}` }); });
    child.on("exit", (code, signal) => {
      for (const w of ["stdout", "stderr"]) if (partial[w].trim()) dlog(`${w}> ${partial[w].trim()}`);
      const done = /REGEN_DONE/.test(full);
      const failed = full.match(/REGEN_FAILED:[^\n]*/);
      const secs = ((Date.now() - t) / 1000).toFixed(1);
      dlog(`agent EXIT code=${code} signal=${signal ?? "-"} after ${secs}s · REGEN_DONE=${done} REGEN_FAILED=${failed ? "yes" : "no"}`);
      resolve({ ok: code === 0 && done, reason: failed ? failed[0] : (full.trim().split("\n").pop() || `exit ${code}`) });
    });
  });
}

async function statSize(p) {
  try { return (await stat(p)).size; } catch { return null; }
}

async function processJob(dir, job) {
  running.add(job.id);
  const t0 = Date.now();
  const dlog = makeJobLogger(dir, job.id, t0);
  const lock = path.join(dir, ".claimed");
  try {
    dlog(`CLAIM · provider=${job.provider || "seedance"} agent=${job.agent || "codex"} dur=${job.durationSec}s slot=${job.label || "?"} src=${job.sourceId || "?"} total=${job.totalSec ?? "?"}s`);
    await writeFile(lock, new Date().toISOString());
    await writeJobStatus(dir, job, { status: "generating", stage: "agent", error: undefined });

    const startSize = await statSize(path.join(dir, "frame_start.png"));
    const endSize = await statSize(path.join(dir, "frame_end.png"));
    dlog(`frames: start=${startSize ?? "MISSING"}B end=${endSize ?? "MISSING"}B`);
    if (startSize == null || endSize == null) {
      dlog(`MARKING ERROR: frames missing — cannot generate`);
      await writeJobStatus(dir, job, { status: "error", stage: "frames", error: "Splice frames missing" });
      return;
    }

    const result = await runAgent(job, dir, dlog);
    dlog(`agent result: ok=${result.ok} reason="${result.reason}"`);

    const clipSize = await statSize(path.join(dir, "clip.mp4"));
    const finalSize = await statSize(path.join(dir, "final.mp4"));
    dlog(`outputs: clip.mp4=${clipSize ?? "absent"}B final.mp4=${finalSize ?? "absent"}B`);

    // The agent reports back to /complete, which moves the job to merging/done.
    // Re-read so we never clobber that. Only intervene if it is still in an
    // agent-owned status.
    const fresh = (await readJob(dir)) || job;
    dlog(`job.json status after agent = ${fresh.status}`);
    if (fresh.status === "awaiting_generation" || fresh.status === "generating") {
      if (result.ok) {
        dlog(`WARN: agent printed REGEN_DONE but status is still ${fresh.status} — /complete likely never landed. Leaving for stale-claim retry.`);
      } else {
        dlog(`MARKING ERROR: ${result.reason}`);
        await writeJobStatus(dir, fresh, { status: "error", stage: "agent", error: result.reason || "Generation failed" });
      }
    }
    const finalStatus = (await readJob(dir))?.status;
    dlog(`FINISHED · status=${finalStatus} · total ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  } catch (err) {
    dlog(`WORKER EXCEPTION: ${err.stack || err.message}`);
  } finally {
    running.delete(job.id);
  }
}

// Throttle repeated "skipping" logs so a long-running claimed job doesn't spam
// the log every poll — we log a skip reason once per (job, reason).
const skipLogged = new Set();
function logSkipOnce(id, reason) {
  const key = `${id}:${reason}`;
  if (skipLogged.has(key)) return;
  skipLogged.add(key);
  if (DEBUG) log(`skip ${id}: ${reason}`);
}

async function tick() {
  if (running.size >= MAX_CONCURRENT) return;
  let entries;
  try { entries = await readdir(REGEN_DIR); } catch (err) { if (DEBUG) log(`tick: cannot read ${REGEN_DIR}: ${err.message}`); return; }

  for (const name of entries) {
    if (running.size >= MAX_CONCURRENT) break;
    if (!name.startsWith("job_")) continue;
    const dir = path.join(REGEN_DIR, name);
    if (running.has(name)) continue;

    const job = await readJob(dir);
    if (!job) continue;
    if (job.status !== "awaiting_generation" && job.status !== "generating") continue;

    const claimed = await exists(path.join(dir, ".claimed"));
    if (claimed) {
      if (await claimStale(dir)) log(`RECLAIM ${name}: previous claim is stale (>${Math.round(STALE_CLAIM_MS / 60000)}m), reprocessing`);
      else { logSkipOnce(name, "already claimed (in progress)"); continue; }
    }
    if (job.status === "generating" && !claimed) log(`RECOVER ${name}: status=generating without claim lock, reprocessing`);

    if (!(await exists(path.join(dir, "frame_start.png"))) || !(await exists(path.join(dir, "frame_end.png")))) {
      logSkipOnce(name, "frames not prepared yet");
      continue;
    }

    skipLogged.delete(`${name}:already claimed (in progress)`);
    skipLogged.delete(`${name}:frames not prepared yet`);
    log(`PICKUP ${name} (status=${job.status}, agent=${job.agent || "codex"}, provider=${job.provider || "seedance"})`);
    void processJob(dir, job);
  }
}

async function main() {
  log(`START · watching ${REGEN_DIR} → ${APP_URL}`);
  log(`config · concurrency=${MAX_CONCURRENT} poll=${POLL_MS}ms staleClaim=${Math.round(STALE_CLAIM_MS / 60000)}m model=${MODEL || "(default)"} debug=${DEBUG}`);
  log(`per-job traces are written to regen/<id>/agent.log`);
  while (true) {
    await tick();
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
}

main().catch((err) => { log("fatal:", err); process.exit(1); });
