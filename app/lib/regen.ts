import { spawn } from "node:child_process";
import { appendFile, mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

/**
 * Server-side media helpers for the clip-regeneration pipeline. Generation
 * itself runs through the Pika MCP (agent-in-the-loop); this module only does
 * the deterministic ffmpeg work the Next backend owns: pulling the start/end
 * frames of a cut and merging a generated clip back into the source in place.
 */

export const REGEN_ROOT = path.join(process.cwd(), "regen");
export const SOURCE_ROOT = path.join(REGEN_ROOT, "sources");

export type RegenStatus = "awaiting_generation" | "generating" | "merging" | "done" | "error";

export type RegenJob = {
  id: string;
  status: RegenStatus;
  startSec: number;
  endSec: number;
  durationSec: number; // slot length, snapped to 5 or 10
  totalSec: number; // source duration
  sourceId: string;
  frameId: string;
  provider?: "seedance" | "kling"; // generation model chosen in the UI
  agent?: "claude" | "codex"; // which MCP agent the worker spawns to drive Pika
  label?: string;
  stage?: string;
  error?: string;
  createdAt: string;
  updatedAt?: string;
};

// Regeneration settings chosen in the UI. Persisted to disk so they survive a
// server restart and are shared by every client + the worker.
export type RegenSettings = { provider: "seedance" | "kling"; agent: "claude" | "codex" };
export const DEFAULT_SETTINGS: RegenSettings = { provider: "seedance", agent: "codex" };
const SETTINGS_FILE = path.join(process.cwd(), ".cerebra-settings.json");

export async function readSettings(): Promise<RegenSettings> {
  try {
    const raw = JSON.parse(await readFile(SETTINGS_FILE, "utf8"));
    return {
      provider: raw.provider === "kling" ? "kling" : "seedance",
      agent: raw.agent === "claude" ? "claude" : "codex",
    };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

export async function writeSettings(next: Partial<RegenSettings>): Promise<RegenSettings> {
  const current = await readSettings();
  const merged: RegenSettings = {
    provider: next.provider === "kling" || next.provider === "seedance" ? next.provider : current.provider,
    agent: next.agent === "claude" || next.agent === "codex" ? next.agent : current.agent,
  };
  await writeFile(SETTINGS_FILE, JSON.stringify(merged, null, 2));
  return merged;
}

export function jobDir(id: string) {
  // Guard against path traversal from a user-supplied id.
  const safe = id.replace(/[^a-zA-Z0-9_-]/g, "");
  return path.join(REGEN_ROOT, safe);
}

export function sourceDir(id: string) {
  const safe = id.replace(/[^a-zA-Z0-9_-]/g, "");
  return path.join(SOURCE_ROOT, safe);
}

export async function readJob(id: string): Promise<RegenJob | null> {
  try {
    return JSON.parse(await readFile(path.join(jobDir(id), "job.json"), "utf8"));
  } catch {
    return null;
  }
}

export async function writeJob(job: RegenJob): Promise<void> {
  await mkdir(jobDir(job.id), { recursive: true });
  await writeFile(path.join(jobDir(job.id), "job.json"), JSON.stringify({ ...job, updatedAt: new Date().toISOString() }, null, 2));
}

function shortCmd(cmd: string, args: string[]) {
  return [cmd, ...args].map((arg) => /\s/.test(arg) ? JSON.stringify(arg) : arg).join(" ");
}

export async function appendJobLog(id: string, message: string): Promise<void> {
  await mkdir(jobDir(id), { recursive: true });
  await appendFile(path.join(jobDir(id), "job.log"), `[${new Date().toISOString()}] ${message}\n`);
}

export async function readJobLogTail(id: string, bytes = 24000): Promise<string> {
  const parts: string[] = [];
  for (const name of ["job.log", "agent.log"]) {
    try {
      const raw = await readFile(path.join(jobDir(id), name), "utf8");
      parts.push(`--- ${name} ---\n${raw.slice(-bytes)}`);
    } catch {
      // Missing logs are normal early in the job lifecycle.
    }
  }
  return parts.join("\n").slice(-(bytes * 2));
}

function run(cmd: string, args: string[], opts: { jobId?: string; label?: string } = {}): Promise<string> {
  return new Promise((resolve, reject) => {
    const t = Date.now();
    if (opts.jobId) void appendJobLog(opts.jobId, `${opts.label ?? "command"} START: ${shortCmd(cmd, args)}`);
    const child = spawn(cmd, args);
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => (stdout += d));
    child.stderr.on("data", (d) => (stderr += d));
    child.on("error", (err) => {
      if (opts.jobId) void appendJobLog(opts.jobId, `${opts.label ?? "command"} SPAWN ERROR: ${err.message}`);
      reject(err);
    });
    child.on("close", (code) => {
      const elapsed = ((Date.now() - t) / 1000).toFixed(2);
      if (code === 0) {
        if (opts.jobId) void appendJobLog(opts.jobId, `${opts.label ?? "command"} OK in ${elapsed}s${stderr.trim() ? `\nstderr:\n${stderr.slice(-4000)}` : ""}`);
        resolve(stdout);
      } else {
        const detail = `${cmd} failed (${code}) after ${elapsed}s`;
        if (opts.jobId) void appendJobLog(opts.jobId, `${opts.label ?? "command"} FAILED: ${detail}\nstderr:\n${stderr.slice(-12000)}\nstdout:\n${stdout.slice(-4000)}`);
        reject(new Error(`${detail}: ${stderr.slice(-1200)}`));
      }
    });
  });
}

type Probe = { width: number; height: number; fps: string; duration: number; hasAudio: boolean };

export async function probe(file: string, opts: { jobId?: string; label?: string } = {}): Promise<Probe> {
  const out = await run("ffprobe", ["-v", "error", "-show_entries",
    "stream=codec_type,width,height,r_frame_rate:format=duration", "-of", "json", file], { ...opts, label: opts.label ?? "ffprobe" });
  const data = JSON.parse(out);
  const video = (data.streams ?? []).find((s: { codec_type: string }) => s.codec_type === "video") ?? {};
  const hasAudio = (data.streams ?? []).some((s: { codec_type: string }) => s.codec_type === "audio");
  return {
    width: Number(video.width) || 1280,
    height: Number(video.height) || 720,
    fps: video.r_frame_rate && video.r_frame_rate !== "0/0" ? video.r_frame_rate : "30",
    duration: Number(data.format?.duration) || 0,
    hasAudio,
  };
}

/** Extract a single frame at `sec` to a PNG (fast input-side seek). */
export async function extractFrame(src: string, sec: number, out: string, opts: { jobId?: string; label?: string } = {}): Promise<void> {
  await run("ffmpeg", ["-y", "-ss", `${sec}`, "-i", src, "-frames:v", "1", "-q:v", "2", out], { ...opts, label: opts.label ?? `extract-frame@${sec.toFixed(3)}s` });
}

/**
 * Replace [startSec, endSec] of `src` with `clip`, re-encoding to one continuous
 * file. The clip is scaled/padded to the source frame size and fps; audio is
 * preserved from the source, with silence covering the clip when the generated
 * clip has no audio track. Empty head/tail segments (cut at the very start/end)
 * are dropped so concat never sees a zero-length input.
 */
export async function mergeReplace(src: string, clip: string, startSec: number, endSec: number, out: string, opts: { jobId?: string } = {}): Promise<void> {
  const s = await probe(src, { jobId: opts.jobId, label: "probe-source-before-merge" });
  const c = await probe(clip, { jobId: opts.jobId, label: "probe-generated-clip" });
  const start = Math.max(0, startSec);
  const end = Math.min(s.duration || endSec, endSec);
  const hasPre = start > 0.04;
  const hasPost = s.duration - end > 0.04;
  const audio = s.hasAudio;

  const filters: string[] = [];
  const vLabels: string[] = [];
  const aLabels: string[] = [];

  if (hasPre) {
    filters.push(`[0:v]trim=start=0:end=${start.toFixed(3)},setpts=PTS-STARTPTS[v0]`);
    if (audio) filters.push(`[0:a]atrim=start=0:end=${start.toFixed(3)},asetpts=PTS-STARTPTS[a0]`);
    vLabels.push("[v0]"); aLabels.push("[a0]");
  }
  // The generated clip, normalised to the source geometry/timebase.
  filters.push(`[1:v]scale=${s.width}:${s.height}:force_original_aspect_ratio=decrease,pad=${s.width}:${s.height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=${s.fps},format=yuv420p,setpts=PTS-STARTPTS[v1]`);
  if (audio) {
    if (c.hasAudio) filters.push(`[1:a]aresample=async=1,asetpts=PTS-STARTPTS[a1]`);
    else filters.push(`anullsrc=channel_layout=stereo:sample_rate=44100,atrim=duration=${(c.duration || (end - start)).toFixed(3)},asetpts=PTS-STARTPTS[a1]`);
  }
  vLabels.push("[v1]"); aLabels.push("[a1]");

  if (hasPost) {
    filters.push(`[0:v]trim=start=${end.toFixed(3)},setpts=PTS-STARTPTS[v2]`);
    if (audio) filters.push(`[0:a]atrim=start=${end.toFixed(3)},asetpts=PTS-STARTPTS[a2]`);
    vLabels.push("[v2]"); aLabels.push("[a2]");
  }

  const n = vLabels.length;
  const concatInputs = audio
    ? vLabels.map((v, i) => `${v}${aLabels[i]}`).join("")
    : vLabels.join("");
  filters.push(`${concatInputs}concat=n=${n}:v=1:a=${audio ? 1 : 0}[vout]${audio ? "[aout]" : ""}`);

  const args = ["-y", "-i", src, "-i", clip, "-filter_complex", filters.join(";"),
    "-map", "[vout]"];
  if (audio) args.push("-map", "[aout]");
  args.push("-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20");
  if (audio) args.push("-c:a", "aac");
  args.push("-movflags", "+faststart", out);
  if (opts.jobId) {
    await appendJobLog(opts.jobId, `merge plan: source=${s.width}x${s.height} fps=${s.fps} duration=${s.duration.toFixed(3)}s audio=${s.hasAudio}; clip=${c.width}x${c.height} duration=${c.duration.toFixed(3)}s audio=${c.hasAudio}; replace=[${start.toFixed(3)}, ${end.toFixed(3)}], segments=${n}`);
  }
  await run("ffmpeg", args, { jobId: opts.jobId, label: "merge-ffmpeg" });
}
