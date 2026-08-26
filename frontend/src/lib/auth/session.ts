/**
 * Auth session skeleton.
 *
 * MegooPM auth is intentionally a stub at the foundation stage: this module
 * defines the seam (cookie name, enable flag, route helpers) that the auth
 * ticket fills in with real token issuance/validation. Enforcement is gated by
 * `NEXT_PUBLIC_AUTH_ENABLED` so the app shell renders during `npm run dev`
 * without a backend session.
 */

/** Name of the cookie that will hold the session token. */
export const SESSION_COOKIE = "megoopm_session";

/** Route users are sent to when unauthenticated. */
export const LOGIN_ROUTE = "/login";

/** Query param used to bounce a user back after login. */
export const REDIRECT_PARAM = "next";

/**
 * Whether route-level auth enforcement is active. Off by default so the shell
 * is browsable in development; flip on in staging/production.
 */
export function isAuthEnabled(): boolean {
  return process.env.NEXT_PUBLIC_AUTH_ENABLED === "true";
}

/** Routes that never require a session (login, health, static handled by matcher). */
export const PUBLIC_ROUTES: readonly string[] = [LOGIN_ROUTE];

export function isPublicRoute(pathname: string): boolean {
  return PUBLIC_ROUTES.some(
    (route) => pathname === route || pathname.startsWith(`${route}/`),
  );
}
