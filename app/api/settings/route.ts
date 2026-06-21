import { NextResponse } from "next/server";
import { readSettings, writeSettings } from "@/app/lib/regen";

export const runtime = "nodejs";

/** GET /api/settings — current regeneration settings (provider + agent). */
export async function GET() {
  return NextResponse.json(await readSettings(), { headers: { "cache-control": "no-store" } });
}

/** POST /api/settings — persist regeneration settings so they survive restart. */
export async function POST(request: Request) {
  let body: { provider?: string; agent?: string };
  try { body = await request.json(); }
  catch { return NextResponse.json({ error: "Expected JSON" }, { status: 400 }); }
  const saved = await writeSettings({
    provider: body.provider as "seedance" | "kling" | undefined,
    agent: body.agent as "claude" | "codex" | undefined,
  });
  return NextResponse.json(saved);
}
