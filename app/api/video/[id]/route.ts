import { NextResponse } from "next/server";

export const runtime = "nodejs";

/**
 * Streams a rendered video stored IN Redis back to the player. Proxies the
 * optimizer's /video/<id> (which reads the MP4 bytes from Redis), passing the
 * Range header through so the <video> element can seek.
 */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const optimizerUrl = process.env.CEREBRA_OPTIMIZER_URL;
  if (!optimizerUrl) {
    return NextResponse.json({ error: "No optimizer configured." }, { status: 503 });
  }
  const range = request.headers.get("range");
  const upstream = await fetch(`${optimizerUrl.replace(/\/$/, "")}/video/${id}`, {
    headers: range ? { range } : {},
  });
  const headers = new Headers();
  for (const h of ["content-type", "content-length", "content-range", "accept-ranges", "cache-control"]) {
    const v = upstream.headers.get(h);
    if (v) headers.set(h, v);
  }
  return new NextResponse(upstream.body, { status: upstream.status, headers });
}
