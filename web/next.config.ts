import type { NextConfig } from "next";

// Where the FastAPI core API runs during `next dev`. In production the static export in `out/`
// is served by the API container itself, so `/api/*` is same-origin and no proxy is needed.
const API_ORIGIN = process.env.SCAMSHIELD_API_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true, // `/check/` -> `out/check/index.html`, which StaticFiles(html=True) serves
  images: { unoptimized: true },
  reactStrictMode: true,
  // Rewrites only apply to the dev server; the export ignores them.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
  },
};

export default nextConfig;
