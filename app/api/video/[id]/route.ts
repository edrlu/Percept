import { NextResponse } from "next/server";

export const runtime = "nodejs";

/**
 * Historical videos were previously streamed from the Python optimizer's Redis
 * cache. That service is no longer required by Studio, so there is no local
 * video cache to read from here.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  return NextResponse.json(
    { error: `Video ${id} is not available in the built-in Studio runtime.` },
    { status: 404 },
  );
}
