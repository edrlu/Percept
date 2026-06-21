import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const maxDuration = 120;

/**
 * Voice → text gateway. Uses OpenAI Whisper directly when OPENAI_API_KEY is
 * present; otherwise the Studio can still work with typed briefs.
 */
export async function POST(request: Request) {
  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) {
    return NextResponse.json(
      { error: "Voice transcription needs OPENAI_API_KEY. You can still type the brief." },
      { status: 503 },
    );
  }

  try {
    const incoming = await request.formData();
    const audio = incoming.get("audio");
    if (!(audio instanceof File)) {
      return NextResponse.json({ error: "No audio file provided." }, { status: 422 });
    }

    const outgoing = new FormData();
    outgoing.append("model", process.env.CEREBRA_WHISPER_MODEL || "whisper-1");
    outgoing.append("file", audio, audio.name || "clip.webm");

    const upstream = await fetch("https://api.openai.com/v1/audio/transcriptions", {
      method: "POST",
      headers: { authorization: `Bearer ${apiKey}` },
      body: outgoing,
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
    const message = error instanceof Error ? error.message : "Transcription failed";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
