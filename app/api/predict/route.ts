import { NextResponse } from "next/server";

export const runtime = "nodejs";
// CPU inference (WhisperX + V-JEPA2-giant + Llama) can take many minutes; allow
// the UI to wait instead of timing out and falling back to demo data. On a fast
// GPU worker this is comfortably under the old 300s.
export const maxDuration = 7200;

/**
 * Thin gateway to the GPU inference worker. Keeping Python and its sizeable
 * neuroscience dependencies outside the Next process makes the web app easy
 * to deploy independently from the model runtime.
 */
export async function POST(request: Request) {
  const workerUrl = process.env.TRIBEV2_API_URL;
  if (!workerUrl) {
    return NextResponse.json(
      { error: "No TRIBE v2 worker configured. Set TRIBEV2_API_URL to enable live inference." },
      { status: 503 },
    );
  }

  try {
    const payload = await request.formData();
    const upstream = await fetch(`${workerUrl.replace(/\/$/, "")}/predict`, {
      method: "POST",
      body: payload,
      signal: AbortSignal.timeout(7_200_000),
    });
    const contentType = upstream.headers.get("content-type") ?? "application/json";
    return new NextResponse(await upstream.arrayBuffer(), {
      status: upstream.status,
      headers: { "content-type": contentType, "cache-control": "no-store" },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Inference worker unavailable";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
