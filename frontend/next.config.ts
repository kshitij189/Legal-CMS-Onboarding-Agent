import type { NextConfig } from "next";

// The dashboard talks to the backend through this same-origin proxy, so the API needs no CORS
// configuration and bearer tokens never leave the dashboard's origin.
const backend = process.env.BACKEND_URL ?? "http://localhost:8200";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
