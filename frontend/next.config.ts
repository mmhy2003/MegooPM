import type { NextConfig } from "next";
import { PHASE_PRODUCTION_BUILD, PHASE_PRODUCTION_SERVER } from "next/constants";

import { buildId } from "./src/lib/build-id";

export default function config(phase: string): NextConfig {
  const production = phase === PHASE_PRODUCTION_BUILD || phase === PHASE_PRODUCTION_SERVER;
  return {
    // Self-contained server bundle for the production image (frontend/Dockerfile,
    // `runner` stage): `.next/standalone/server.js` + traced node_modules only.
    output: "standalone",
    // Ties every page to the build that served it. Next reloads a page in full,
    // rather than half-loading it, when a navigation reaches a newer build; and
    // an open tab compares it with /version.json to offer a reload
    // (components/update-notice.tsx). None on the dev server.
    deploymentId: production ? buildId(process.cwd(), process.env) : undefined,
  };
}
