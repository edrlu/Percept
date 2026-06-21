import { NextResponse } from "next/server";

export const runtime = "nodejs";

/**
 * Stage 2 placeholder. Stage 1 optimization is now built into Next; rendering
 * still needs a Pika integration in this process, so fail clearly instead of
 * proxying to a removed optimizer service.
 */
export async function POST() {
  return NextResponse.json(
    {
      status: "unavailable",
      message:
        "Prompt optimization is ready. Video generation is not wired into the Next.js process yet; copy the payload and run it through Pika/Seedance.",
    },
    {
      status: 501,
      headers: { "cache-control": "no-store" },
    },
  );
}
