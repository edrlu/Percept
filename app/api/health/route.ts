import { NextResponse } from "next/server";

import { studioHealth } from "@/app/lib/studioOptimizer";

export const runtime = "nodejs";

/**
 * Studio readiness probe. Reports the built-in optimizer/corpus status; no
 * external optimizer service is required.
 */
export async function GET() {
  return NextResponse.json(studioHealth(), {
    headers: { "cache-control": "no-store" },
  });
}
