import { readFile } from "node:fs/promises";
import path from "node:path";
import { jobDir } from "@/app/lib/regen";

export const runtime = "nodejs";

const ALLOWED: Record<string, string> = {
  "frame_start.png": "image/png",
  "frame_end.png": "image/png",
  "clip.mp4": "video/mp4",
  "final.mp4": "video/mp4",
};

/** GET /api/regenerate/file?job=<id>&name=<frame_start.png|final.mp4|...> */
export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const id = params.get("job");
  const name = params.get("name") ?? "";
  const type = ALLOWED[name];
  if (!id || !type) return new Response("Not found", { status: 404 });

  try {
    const data = await readFile(path.join(jobDir(id), name));
    const headers: Record<string, string> = { "content-type": type, "cache-control": "no-store" };
    if (name === "final.mp4") headers["content-disposition"] = `attachment; filename="cerebra_regenerated.mp4"`;
    return new Response(new Uint8Array(data), { headers });
  } catch {
    return new Response("Not found", { status: 404 });
  }
}
