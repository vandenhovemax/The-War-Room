import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Prevent Next.js from stripping trailing slashes — FastAPI requires them,
  // and the mismatch causes redirect loops through the proxy.
  skipTrailingSlashRedirect: true,
  // Dev-time typecheck only — don't block production builds on pre-existing
  // TS errors in unrelated files. `tsc --noEmit` in dev still surfaces them.
  typescript: {
    ignoreBuildErrors: true,
  },
  eslint: {
    ignoreDuringBuilds: true,
  },
  // Default proxy timeout is 30s which is too short for optimizer/cross-strategy
  // builds that can take up to 60 minutes with complex math.
  experimental: {
    proxyTimeout: 3_660_000,
  },
  async rewrites() {
    // BACKEND_INTERNAL_URL lets the Docker deployment point at the backend
    // container (http://warroom-backend:8055); default keeps dev on Windows
    // working unchanged.
    const backend = process.env.BACKEND_INTERNAL_URL || "http://localhost:8055";
    return [
      {
        source: "/api/:path*",
        destination: `${backend}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
