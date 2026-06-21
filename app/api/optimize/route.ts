import { NextResponse } from "next/server";

import { optimizeStudioBrief, RequestError } from "@/app/lib/studioOptimizer";

export const runtime = "nodejs";
export const maxDuration = 300;

const JSON_HEADERS = {
  "content-type": "application/json",
  "cache-control": "no-store",
} as const;

/**
 * Stage 1 optimizer.
 *
 * Primary path: proxy to the Python pipeline, which owns the REAL Redis usage —
 * query embedding + vector (cosine) search over the ad-knowledge index, the
 * semantic prompt cache, and per-session conversation history — then optimizes
 * with Opus 4.8. This is the path that actually grounds generation in Redis
 * retrieval.
 *
 * Fallback: only if the pipeline is unreachable do we run the in-process keyword
 * optimizer, so a live demo never hard-fails. It does not touch Redis and clearly
 * self-reports `rag.backend = "local"`.
 */
export async function POST(request: Request) {
  const body = await request.text();
  const pipelineUrl = process.env.CEREBRA_OPTIMIZER_URL;

  if (pipelineUrl) {
    try {
      const upstream = await fetch(`${pipelineUrl.replace(/\/$/, "")}/optimize`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body,
        signal: AbortSignal.timeout(295_000),
      });

      // Success or a client error (e.g. empty brief) is authoritative — return it.
      if (upstream.ok || (upstream.status >= 400 && upstream.status < 500)) {
        return new NextResponse(await upstream.text(), {
          status: upstream.status,
          headers: JSON_HEADERS,
        });
      }
      // 5xx → fall through to the offline stub below.
    } catch {
      // network error / timeout → fall through to the offline stub below.
    }
  }

  // Offline fallback: in-process keyword optimizer (no Redis).
  try {
    const parsed = JSON.parse(body || "{}");
    return NextResponse.json(optimizeStudioBrief(parsed), {
      headers: { "cache-control": "no-store" },
    });
  } catch (error) {
    const status = error instanceof RequestError ? error.status : 500;
    const message = error instanceof Error ? error.message : "Optimization failed.";
    return NextResponse.json({ error: message }, { status });
  }
}
