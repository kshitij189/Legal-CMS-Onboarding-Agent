import type { NextConfig } from "next";

// The dashboard talks to the backend through this same-origin proxy, so the API needs no CORS
// configuration and bearer tokens never leave the dashboard's origin.
const backend = process.env.BACKEND_URL ?? "http://localhost:8200";

const nextConfig: NextConfig = {
  experimental: {
    // Generating proposals waits on the LLM (and on Groq's tokens-per-minute pacing), which can take
    // minutes. Next's default of 30 s answers with a 500 while the backend is still working.
    proxyTimeout: 300_000,
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
