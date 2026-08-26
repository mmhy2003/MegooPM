/**
 * Auth endpoint bindings over the API client.
 *
 * These call the backend JWT endpoints published in `backend/openapi.json`.
 * Login and refresh pass an explicit `token` so {@link apiFetch} does not
 * attach the (possibly expired) session token or trigger the refresh retry —
 * that would recurse, since refresh is itself how a token is renewed.
 */
import { apiFetch } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";
import type { TokenPair } from "@/lib/auth/session";

export type CurrentUser = Schemas["UserRead"];

/** Exchange credentials for a token pair. */
export function login(email: string, password: string): Promise<TokenPair> {
  return apiFetch<TokenPair>("/api/v1/auth/login", {
    method: "POST",
    body: { email, password },
    token: null,
  });
}

/** Exchange a refresh token for a fresh token pair. */
export function refresh(refreshToken: string): Promise<TokenPair> {
  return apiFetch<TokenPair>("/api/v1/auth/refresh", {
    method: "POST",
    body: { refresh_token: refreshToken },
    token: null,
  });
}

/** Fetch the authenticated user (uses the session token via the provider). */
export function fetchCurrentUser(): Promise<CurrentUser> {
  return apiFetch<CurrentUser>("/api/v1/users/me", { method: "GET" });
}
