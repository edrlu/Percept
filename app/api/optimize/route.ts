import { NextResponse } from "next/server";

import { optimizeStudioBrief, RequestError } from "@/app/lib/studioOptimizer";

export const runtime = "nodejs";

/**
 * Stage 1 optimizer. This runs in-process: it retrieves from the bundled
 * ad-knowledge corpus, resolves Seedance settings, and assembles the
 * model-ready payload.
 */
export async function POST(request: Request) {
  try {
    const body = await request.json();
    return NextResponse.json(optimizeStudioBrief(body), {
      headers: { "cache-control": "no-store" },
    });
  } catch (error) {
    const status = error instanceof RequestError ? error.status : 500;
    const message = error instanceof Error ? error.message : "Optimization failed.";
    return NextResponse.json({ error: message }, { status });
  }
}
