import { NextResponse } from "next/server";

export const runtime = "nodejs";
// A cold first inference (loading ~25GB of weights into the GPU) can take several
// minutes; allow the UI to wait instead of timing out and falling back to demo
// data. (A warm worker is seconds.)
export const maxDuration = 14400;

// Node's fetch (undici) has a ~300s headersTimeout that would still cut off a slow
// cold inference even with maxDuration raised. Lift it globally — best-effort:
// no-ops if the bundled undici isn't importable.
import("next/dist/compiled/undici")
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  .then((u: any) => u.setGlobalDispatcher?.(new u.Agent({ headersTimeout: 0, bodyTimeout: 0 })))
  .catch(() => {});

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
      signal: AbortSignal.timeout(14_400_000),
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
