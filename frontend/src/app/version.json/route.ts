/**
 * The build this server runs (see `deploymentId` in next.config.ts). An open
 * tab compares it with its own to notice a new deployment
 * (components/update-notice.tsx).
 *
 * Open without a session: the path has a dot, so the auth proxy's matcher
 * skips it, and the ID says nothing about the installation.
 */
export const dynamic = "force-dynamic";

export function GET(): Response {
  return Response.json(
    { id: process.env.NEXT_DEPLOYMENT_ID || null },
    // Never from a cache: a stale answer would hide the update it is for.
    { headers: { "Cache-Control": "no-store" } },
  );
}
