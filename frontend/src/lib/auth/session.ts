/**
 * Auth session storage and routing seam.
 *
 * Tokens are kept in cookies (not `localStorage`) so the `proxy` guard, which
 * only runs on the server before a request completes, can make an optimistic
 * "is there a session?" check on hard navigations. Both cookies are readable by
 * JS: the browser talks to the FastAPI backend cross-origin and must attach
 * `Authorization: Bearer <access>` itself, so the access token has to live in
 * script-reachable storage regardless.
 *
 * Trade-off: script-readable cookies are exposed to XSS the same way
 * `localStorage` would be. A future hardening is a Backend-for-Frontend that
 * proxies API calls and keeps `HttpOnly` refresh cookies server-side — see the
 * Next.js BFF guide. For now this keeps the client a thin SPA over the backend.
 */
import type { Schemas } from "@/lib/api/types";

/** Issued access + refresh tokens (`TokenPair` from the backend contract). */
export type TokenPair = Schemas["TokenPair"];

/** Name of the cookie holding the access token. */
export const SESSION_COOKIE = "megoopm_session";

/** Name of the cookie holding the refresh token. */
export const REFRESH_COOKIE = "megoopm_refresh";

/** Route users are sent to when unauthenticated. */
export const LOGIN_ROUTE = "/login";

/** Query param used to bounce a user back after login. */
export const REDIRECT_PARAM = "next";

/** Default landing route after a successful login. */
export const DEFAULT_AUTHED_ROUTE = "/";

/** How long the session cookies live, in seconds (7 days). */
const COOKIE_MAX_AGE = 60 * 60 * 24 * 7;

/**
 * Whether route-level auth enforcement is active. On by default now that auth
 * is wired to the backend; set `NEXT_PUBLIC_AUTH_ENABLED=false` to browse the
 * shell without a session (e.g. local UI work with no backend running).
 */
export function isAuthEnabled(): boolean {
  return process.env.NEXT_PUBLIC_AUTH_ENABLED !== "false";
}

/** Route where a user asks for a reset link. */
export const FORGOT_PASSWORD_ROUTE = "/forgot-password";

/** Route the emailed link lands on. */
export const RESET_PASSWORD_ROUTE = "/reset-password";

/** Route the invitation email lands on. */
export const ACCEPT_INVITE_ROUTE = "/accept-invite";

/** Routes that never require a session (login, health, static handled by matcher). */
export const PUBLIC_ROUTES: readonly string[] = [
  LOGIN_ROUTE,
  FORGOT_PASSWORD_ROUTE,
  RESET_PASSWORD_ROUTE,
  ACCEPT_INVITE_ROUTE,
];

export function isPublicRoute(pathname: string): boolean {
  return PUBLIC_ROUTES.some(
    (route) => pathname === route || pathname.startsWith(`${route}/`),
  );
}

// --- Client-side cookie token store -------------------------------------------
// These helpers touch `document` and must only run in the browser. The `proxy`
// guard reads cookies via the request object, never through this module.

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${name}=`;
  for (const part of document.cookie.split("; ")) {
    if (part.startsWith(prefix)) {
      return decodeURIComponent(part.slice(prefix.length));
    }
  }
  return null;
}

function writeCookie(name: string, value: string): void {
  if (typeof document === "undefined") return;
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie =
    `${name}=${encodeURIComponent(value)}; Path=/; Max-Age=${COOKIE_MAX_AGE}` +
    `; SameSite=Lax${secure}`;
}

function deleteCookie(name: string): void {
  if (typeof document === "undefined") return;
  document.cookie = `${name}=; Path=/; Max-Age=0; SameSite=Lax`;
}

export function getAccessToken(): string | null {
  return readCookie(SESSION_COOKIE);
}

export function getRefreshToken(): string | null {
  return readCookie(REFRESH_COOKIE);
}

/** Persist a freshly issued token pair to the session cookies. */
export function persistSession(tokens: TokenPair): void {
  writeCookie(SESSION_COOKIE, tokens.access_token);
  writeCookie(REFRESH_COOKIE, tokens.refresh_token);
}

/** Drop the session cookies (logout / failed refresh). */
export function clearSession(): void {
  deleteCookie(SESSION_COOKIE);
  deleteCookie(REFRESH_COOKIE);
}

/** True when a session cookie is present (client-side optimistic check). */
export function hasSession(): boolean {
  return getAccessToken() !== null || getRefreshToken() !== null;
}
