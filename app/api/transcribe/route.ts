import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const maxDuration = 120;

/**
 * Voice → text gateway. Forwards recorded audio (multipart) to the Python
 * optimizer's /transcribe, which runs OpenAI Whisper. Kept as a proxy so the
 * browser never holds the OpenAI key.
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
    const contentType = request.headers.get("content-type") ?? "application/octet-stream";
    const body = Buffer.from(await request.arrayBuffer());
    const upstream = await fetch(`${optimizerUrl.replace(/\/$/, "")}/transcribe`, {
      method: "POST",
      headers: { "content-type": contentType },
      body,
      signal: AbortSignal.timeout(115_000),
    });
    return new NextResponse(await upstream.arrayBuffer(), {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "application/json",
        "cache-control": "no-store",
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Transcription proxy failed";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
