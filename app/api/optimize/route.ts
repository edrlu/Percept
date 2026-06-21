import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const maxDuration = 300;

/**
 * Stage 1 gateway. Proxies a creative brief to the Python RAG optimizer
 * (pipeline/), which returns the assembled video-model payload — SYSTEM prompt +
 * research/retrieval context + Seedance 2.0 generation skill. Kept out of the Next process so the
 * Redis + embedding + LLM stack deploys independently from the web app.
 */
export async function POST(request: Request) {
  const optimizerUrl = process.env.CEREBRA_OPTIMIZER_URL;
  if (!optimizerUrl) {
    return NextResponse.json(
      { error: "No optimizer configured. Set CEREBRA_OPTIMIZER_URL to enable Stage 1." },
      { status: 503 },
    );
  }

  try {
    const body = await request.text();
    const upstream = await fetch(`${optimizerUrl.replace(/\/$/, "")}/optimize`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body,
      signal: AbortSignal.timeout(290_000),
    });
    const contentType = upstream.headers.get("content-type") ?? "application/json";
    return new NextResponse(await upstream.arrayBuffer(), {
      status: upstream.status,
      headers: { "content-type": contentType, "cache-control": "no-store" },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Optimizer unavailable";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
