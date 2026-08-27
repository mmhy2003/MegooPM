import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Self-contained server bundle for the production image (frontend/Dockerfile,
  // `runner` stage): `.next/standalone/server.js` + traced node_modules only.
  output: "standalone",
};

export default nextConfig;
