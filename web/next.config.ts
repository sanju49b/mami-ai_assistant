import type { NextConfig } from "next";

// Static export: the FastAPI backend serves these files, so the web app and
// the API share one origin (simple, secure cookies; no CORS).
const config: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default config;
