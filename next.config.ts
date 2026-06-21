import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Lets run.sh safely launch another local instance when the default address
  // is already in use. Normal `npm run dev` behavior remains unchanged.
  ...(process.env.NEXT_DIST_DIR ? { distDir: process.env.NEXT_DIST_DIR } : {}),
  // CPU inference (WhisperX + V-JEPA2-giant + Llama) can take many minutes. The
  // dev server otherwise cuts the request off (~5 min Node default) and the UI
  // falls back to demo data. Extend it so /api/predict can wait for real output.
  experimental: { proxyTimeout: 7_200_000 },
};

export default nextConfig;
