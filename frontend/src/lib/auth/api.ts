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
export type MfaRequired = Schemas["MfaRequired"];
export type MfaVerifyResponse = Schemas["MfaVerifyResponse"];
/** A signed-in pair, or a challenge to present with a code. */
export type LoginResult = TokenPair | MfaRequired;

/** Exchange credentials for a token pair — or a second-factor challenge. */
export function login(email: string, password: string): Promise<LoginResult> {
  return apiFetch<LoginResult>("/api/v1/auth/login", {
    method: "POST",
    body: { email, password },
    token: null,
  });
}

export type MfaMethod = NonNullable<MfaRequired["methods"]>[number];
export type PasskeyOptions = Schemas["PasskeyOptions"];

/** Options for answering the challenge with a passkey. */
export function passkeyOptions(mfaToken: string): Promise<PasskeyOptions> {
  return apiFetch<PasskeyOptions>("/api/v1/auth/mfa/passkey/options", {
    method: "POST",
    body: { mfa_token: mfaToken },
    token: null,
  });
}

/** Exchange the challenge token plus an assertion for the real pair. */
export function passkeyVerify(
  mfaToken: string,
  nonce: string,
  credential: unknown,
): Promise<MfaVerifyResponse> {
  return apiFetch<MfaVerifyResponse>("/api/v1/auth/mfa/passkey/verify", {
    method: "POST",
    body: { mfa_token: mfaToken, nonce, credential },
    token: null,
  });
}

export function isMfaRequired(result: LoginResult): result is MfaRequired {
  return "mfa_required" in result && result.mfa_required === true;
}

/** Exchange a challenge token plus a code for the real pair. */
export function verifyMfa(mfaToken: string, code: string): Promise<MfaVerifyResponse> {
  return apiFetch<MfaVerifyResponse>("/api/v1/auth/mfa/verify", {
    method: "POST",
    body: { mfa_token: mfaToken, code },
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

export type AuthCapabilities = Schemas["AuthCapabilities"];

/** What the login page may offer before anyone is signed in. */
export function fetchCapabilities(): Promise<AuthCapabilities> {
  return apiFetch<AuthCapabilities>("/api/v1/auth/capabilities", {
    method: "GET",
    token: null,
  });
}

/**
 * Ask for a reset link. Resolves the same way whether or not the address is
 * registered — the backend never says, and neither must the page.
 */
export function requestPasswordReset(email: string): Promise<void> {
  return apiFetch<void>("/api/v1/auth/forgot-password", {
    method: "POST",
    body: { email },
    token: null,
  });
}

/** Spend a reset token. A refused token is a 400 with one message for every reason. */
export function resetPassword(token: string, newPassword: string): Promise<void> {
  return apiFetch<void>("/api/v1/auth/reset-password", {
    method: "POST",
    body: { token, new_password: newPassword },
    token: null,
  });
}

/** Spend an invitation token. Refused tokens are a 400 with one message. */
export function acceptInvite(token: string, fullName: string, password: string): Promise<void> {
  return apiFetch<void>("/api/v1/auth/accept-invite", {
    method: "POST",
    body: { token, full_name: fullName, password },
    token: null,
  });
}
