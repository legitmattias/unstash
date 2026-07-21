import type { NextConfig } from "next";

const API_ORIGIN = process.env.API_INTERNAL_ORIGIN ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    // Production routes /api/* to FastAPI through Caddy before it reaches
    // Next. Development has no Caddy, so same-origin browser /api calls
    // (login, click reporting) are proxied to the API here.
    if (process.env.NODE_ENV !== "development") {
      return [];
    }
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
  },
};

export default nextConfig;
