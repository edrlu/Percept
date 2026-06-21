import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";

import { readSettings, sourceDir } from "@/app/lib/regen";

export const runtime = "nodejs";
export const maxDuration = 300;

const JSON_HEADERS = {
  "content-type": "application/json",
  "cache-control": "no-store",
} as const;

/**
 * Stage 2 — render the optimized creative through Pika's Seedance 2.0 (or Kling)
 * provider, the SAME way clip regeneration does it: agent-in-the-loop.
 *
 * The MCP `generate_video` tool is only reachable by an agent CLI (codex / claude
 * — both already have the Pika MCP connected via `.mcp.json`), not by a plain
 * HTTP call, so this route spawns a headless agent to run one text→video
 * generation and print the finished URL on a `GEN_URL:` sentinel line. No Pika
 * OAuth token or Python pipeline is needed in the Next process.
 *
 * Unlike clip regeneration (3 concurrent clips + ffmpeg merge → background worker
 * + polling), the Studio renders one clip at a time and the UI already blocks on
 * a single request, so we spawn + await inline and return the URL directly.
 */

const ROOT = process.cwd();
// Leave headroom under maxDuration so we kill a stuck agent and still respond.
const AGENT_TIMEOUT_MS = 285_000;

// Provider-hosted URLs are often playable in a <video> tag but blocked from a
// browser fetch by CORS. Save each completed render behind our own origin so it
// can move reliably from Create into the file-based Refine workflow.
async function cacheGeneratedVideo(url: string): Promise<string> {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`The video provider returned ${response.status} while loading the render.`);
  const bytes = Buffer.from(await response.arrayBuffer());
  if (!bytes.length) throw new Error("The video provider returned an empty render.");

  const id = `generated_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const dir = sourceDir(id);
  await mkdir(path.join(dir, "frames"), { recursive: true });
  await writeFile(path.join(dir, "source.mp4"), bytes);
  return `/api/regenerate/file?source=${id}&name=source.mp4`;
}

type GenBody = { prompt?: string; aspect_ratio?: string; duration_seconds?: number };

const VALID_ASPECTS = new Set(["9:16", "16:9", "1:1", "3:4", "4:3", "21:9"]);

function resolveAgent(preferred: "claude" | "codex"): { agent: "claude" | "codex"; cmd: string } | null {
  // run.sh exports the resolved invocation (e.g. "npx --no-install claude") when
  // the CLI isn't a global binary; fall back to the bare name on PATH otherwise.
  const order: ("claude" | "codex")[] = preferred === "claude" ? ["claude", "codex"] : ["codex", "claude"];
  for (const agent of order) {
    const override = agent === "claude" ? process.env.REGEN_CLAUDE_CMD : process.env.REGEN_CODEX_CMD;
    if (override) return { agent, cmd: override };
  }
  // No explicit override — assume the preferred CLI is on PATH (spawn surfaces a
  // clear ENOENT if it isn't, which we translate into a helpful message).
  return { agent: preferred, cmd: preferred };
}

function buildPrompt(provider: "seedance" | "kling", prompt: string, aspect: string, duration: number): string {
  const step = provider === "kling"
    ? `2. Call mcp__pika__generate_video with: provider="kling", mode="text_to_video", ` +
      `prompt=<the creative prompt below>, aspect_ratio="${aspect}", duration=${duration >= 8 ? 10 : 5} (Kling accepts only 5 or 10), ` +
      `quality_mode="pro", sound=true. Do NOT pass background=true.`
    : `2. Call mcp__pika__generate_video with: provider="seedance", mode="text_to_video", ` +
      `prompt=<the creative prompt below>, aspect_ratio="${aspect}", duration=${duration}, ` +
      `fast=true, resolution="720p", seedance_backend="ark", sound=true. ` +
      `Do NOT pass negative_prompt / quality_mode / prompt_adherence (Seedance rejects those), and do NOT pass background=true. ` +
      `(fast tier requires 720p and finishes within the server budget so the call returns the URL inline.)`;
  return [
    "You are an automated worker performing ONE text-to-video generation for the Cerebra Studio.",
    "Work autonomously and do NOT ask questions — you have the permissions you need.",
    "",
    `Model: ${provider === "kling" ? "Kling" : "Seedance 2.0"} (provider="${provider}")`,
    "",
    "Follow these steps exactly:",
    "1. (no input frames — this is a pure text-to-video render)",
    step,
    "   If the call falls back to {task_id,status} instead of returning a URL, poll mcp__pika__task_status",
    "   in a tight loop until status is completed/failed/cancelled, passing the task_id EXACTLY as returned",
    "   (it is a long JWT — copy it verbatim, never truncate or edit it).",
    "3. When you have the finished video URL, print it on its OWN line in EXACTLY this form:",
    "   GEN_URL:<https url>",
    "   Print nothing else on that line. If generation fails, print a line starting with \"GEN_FAILED:\" and the reason.",
    "",
    "End with a single line: either the GEN_URL: line above or \"GEN_FAILED: <reason>\".",
    "",
    "----- CREATIVE PROMPT -----",
    prompt,
  ].join("\n");
}

function runAgent(agent: "claude" | "codex", cmd: string, promptText: string): Promise<{ url?: string; reason?: string }> {
  return new Promise((resolve) => {
    let argv: string[];
    if (agent === "claude") {
      argv = [
        "-p", promptText,
        "--add-dir", ROOT,
        "--allowedTools", "Read,Bash,mcp__pika__generate_video,mcp__pika__task_status,mcp__pika__upload_asset",
        "--dangerously-skip-permissions",
      ];
    } else {
      argv = ["exec", "--cd", ROOT, "--dangerously-bypass-approvals-and-sandbox", promptText];
    }
    // Honor multi-token overrides like "npx --no-install claude".
    const parts = cmd.split(/\s+/).filter(Boolean);
    const bin = parts[0];
    if (parts.length > 1) argv = [...parts.slice(1), ...argv];

    const child = spawn(bin, argv, { cwd: ROOT, stdio: ["ignore", "pipe", "pipe"], env: process.env });
    let out = "";
    child.stdout.on("data", (d) => { out = (out + d.toString()).slice(-60000); });
    child.stderr.on("data", (d) => { out = (out + d.toString()).slice(-60000); });

    const timer = setTimeout(() => { child.kill("SIGKILL"); }, AGENT_TIMEOUT_MS);
    child.on("error", (err) => {
      clearTimeout(timer);
      const why = (err as NodeJS.ErrnoException).code === "ENOENT"
        ? `The "${agent}" CLI was not found. Install it (or set REGEN_${agent.toUpperCase()}_CMD) to enable generation.`
        : `Failed to start ${agent}: ${err.message}`;
      resolve({ reason: why });
    });
    child.on("exit", (code, signal) => {
      clearTimeout(timer);
      const urlMatch = out.match(/GEN_URL:\s*(https?:\/\/\S+)/g);
      if (urlMatch?.length) {
        const last = urlMatch[urlMatch.length - 1].replace(/GEN_URL:\s*/, "").trim();
        resolve({ url: last });
        return;
      }
      const failed = out.match(/GEN_FAILED:[^\n]*/);
      if (signal === "SIGKILL") { resolve({ reason: "Generation timed out before the agent returned a video." }); return; }
      resolve({ reason: failed ? failed[0] : `Agent finished without a video URL (exit ${code}).` });
    });
  });
}

export async function POST(request: Request) {
  let body: GenBody;
  try { body = (await request.json()) as GenBody; }
  catch { return NextResponse.json({ status: "error", message: "Expected JSON." }, { status: 400, headers: JSON_HEADERS }); }

  const prompt = (body.prompt || "").trim();
  if (!prompt) return NextResponse.json({ status: "error", message: "Prompt is empty." }, { status: 422, headers: JSON_HEADERS });

  const aspect = body.aspect_ratio && VALID_ASPECTS.has(body.aspect_ratio) ? body.aspect_ratio : "3:4";
  const duration = Math.max(4, Math.min(15, Math.round(body.duration_seconds || 10)));

  const settings = await readSettings();
  const resolved = resolveAgent(settings.agent);
  if (!resolved) {
    return NextResponse.json(
      { status: "unavailable", message: "No generation agent (claude or codex) is available." },
      { headers: JSON_HEADERS },
    );
  }

  const promptText = buildPrompt(settings.provider, prompt, aspect, duration);
  const result = await runAgent(resolved.agent, resolved.cmd, promptText);

  if (result.url) {
    try {
      const localVideoUrl = await cacheGeneratedVideo(result.url);
      return NextResponse.json(
        { status: "completed", video_url: localVideoUrl, provider: settings.provider, agent: resolved.agent },
        { headers: JSON_HEADERS },
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : "The generated video could not be prepared for refining.";
      return NextResponse.json({ status: "error", message }, { status: 502, headers: JSON_HEADERS });
    }
  }
  return NextResponse.json(
    { status: "error", message: result.reason || "Generation failed.", agent: resolved.agent },
    { headers: JSON_HEADERS },
  );
}
