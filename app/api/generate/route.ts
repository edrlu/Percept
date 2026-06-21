import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const maxDuration = 600;

/**
 * Stage 2 gateway. Sends the optimized prompt to the Python service, which
 * renders it via Pika's Seedance 2.0 provider—or returns the cached render—and
 * replies with the video URL the Studio phone auto-plays.
 */
export async function POST(request: Request) {
  const optimizerUrl = process.env.CEREBRA_OPTIMIZER_URL;
  if (!optimizerUrl) {
    return NextResponse.json(
      { error: "No optimizer configured. Set CEREBRA_OPTIMIZER_URL." },
      { status: 503 },
    );
  }
  try {
    const body = await request.text();
    const upstream = await fetch(`${optimizerUrl.replace(/\/$/, "")}/generate`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body,
      signal: AbortSignal.timeout(590_000),
    });
    return new NextResponse(await upstream.arrayBuffer(), {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "application/json",
        "cache-control": "no-store",
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Generate proxy failed";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
