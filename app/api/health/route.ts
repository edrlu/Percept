import { NextResponse } from "next/server";

export const runtime = "nodejs";

/**
 * Live readiness probe for the Studio. Proxies the Python RAG optimizer's
 * /health so the UI can surface the real Redis vector-store status (endpoint,
 * index, vector count) before and after a run — proof the RAG context comes
 * from Redis Vector Search, not a mock.
 */
export async function GET() {
  const optimizerUrl = process.env.CEREBRA_OPTIMIZER_URL;
  if (!optimizerUrl) {
    return NextResponse.json(
      { ready: false, error: "No optimizer configured. Set CEREBRA_OPTIMIZER_URL." },
      { status: 503 },
    );
  }
  try {
    const upstream = await fetch(`${optimizerUrl.replace(/\/$/, "")}/health`, {
      signal: AbortSignal.timeout(15_000),
    });
    return new NextResponse(await upstream.arrayBuffer(), {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "application/json",
        "cache-control": "no-store",
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Optimizer unavailable";
    return NextResponse.json({ ready: false, error: message }, { status: 502 });
  }
}
