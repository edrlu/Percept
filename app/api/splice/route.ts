import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const maxDuration = 300;

/**
 * Gateway to the worker's ffmpeg-backed splice endpoint. The browser posts the
 * original video plus the cut ranges (as fractions of the clip) and gets back
 * the spliced mp4, which it then re-analyses like any other upload.
 */
export async function POST(request: Request) {
  const workerUrl = process.env.TRIBEV2_API_URL;
  if (!workerUrl) {
    return NextResponse.json(
      { error: "No TRIBE v2 worker configured. Set TRIBEV2_API_URL to enable splicing." },
      { status: 503 },
    );
  }

  try {
    const payload = await request.formData();
    const upstream = await fetch(`${workerUrl.replace(/\/$/, "")}/splice`, {
      method: "POST",
      body: payload,
      signal: AbortSignal.timeout(290_000),
    });
    const contentType = upstream.headers.get("content-type") ?? "application/json";
    return new NextResponse(await upstream.arrayBuffer(), {
      status: upstream.status,
      headers: { "content-type": contentType, "cache-control": "no-store" },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Splice worker unavailable";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
