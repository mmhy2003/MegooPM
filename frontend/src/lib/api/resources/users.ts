/**
 * Typed client for user management and self-service account endpoints.
 *
 * Admin-only: list/create/update/resetPassword/remove. Any signed-in user:
 * updateMe/changeMyPassword. Shapes come from the generated OpenAPI schema;
 * the API's lock-out rules surface as 409s with a human-readable `detail`.
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type User = Schemas["UserRead"];
export type UserCreate = Schemas["UserCreate"];
export type UserUpdate = Schemas["UserUpdate"];
export type PasswordReset = Schemas["PasswordReset"];
export type PasswordChange = Schemas["PasswordChange"];
export type ProfileUpdate = Schemas["ProfileUpdate"];
export type UserRole = Schemas["UserRole"];
export type UserInvite = Schemas["UserInvite"];
export type TotpSetup = Schemas["TotpSetup"];
export type TotpCodes = Schemas["TotpCodes"];
export type Passkey = Schemas["PasskeyRead"];
export type PasskeyRegister = Schemas["PasskeyRegisterRequest"];
export type PasskeyOptions = Schemas["PasskeyOptions"];

const BASE = "/api/v1/users";

export const users = {
  list: () => api.get<User[]>(BASE),
  create: (body: UserCreate) => api.post<User>(BASE, body),
  update: (id: number, body: UserUpdate) => api.patch<User>(`${BASE}/${id}`, body),
  resetPassword: (id: number, body: PasswordReset) => api.put<void>(`${BASE}/${id}/password`, body),
  remove: (id: number) => api.delete<void>(`${BASE}/${id}`),
  /** Create an invited (inactive) user and email them the link. 409 if taken or mail is off. */
  invite: (body: UserInvite) => api.post<User>(`${BASE}/invite`, body),
  /** A fresh link for a user who has not accepted yet. 409 once they have. */
  resendInvite: (id: number) => api.post<void>(`${BASE}/${id}/invite`, {}),
  /** The caller's own profile (display name only). */
  updateMe: (body: ProfileUpdate) => api.patch<User>(`${BASE}/me`, body),
  /** The caller's own password; 400 when the current password is wrong. */
  changeMyPassword: (body: PasswordChange) => api.put<void>(`${BASE}/me/password`, body),
  /** Start enrolling an authenticator app. 2FA stays off until confirmed. */
  totpSetup: () => api.post<TotpSetup>(`${BASE}/me/totp/setup`, {}),
  /** Prove the app works; returns the recovery codes exactly once. */
  totpEnable: (code: string) => api.post<TotpCodes>(`${BASE}/me/totp/enable`, { code }),
  /** Turn 2FA off. A valid code is required. */
  totpDisable: (code: string) => api.post<void>(`${BASE}/me/totp/disable`, { code }),
  /** Replace every recovery code. A valid code is required. */
  totpRegenerate: (code: string) => api.post<TotpCodes>(`${BASE}/me/totp/recovery-codes`, { code }),
  /** Admin: turn off another user's 2FA. No code — the lost-phone backstop. */
  adminTotpDisable: (id: number) => api.post<void>(`${BASE}/${id}/totp/disable`, {}),
  /** Registered passkeys: name and dates only. */
  passkeys: () => api.get<Passkey[]>(`${BASE}/me/passkeys`),
  /** Start registering a passkey. A valid code is required. */
  passkeyOptions: (code: string) =>
    api.post<PasskeyOptions>(`${BASE}/me/passkeys/options`, { code }),
  /** Finish registering with the browser's credential. */
  registerPasskey: (body: PasskeyRegister) => api.post<Passkey>(`${BASE}/me/passkeys`, body),
  /** Remove one. A valid code is required; a POST so the body survives proxies. */
  removePasskey: (id: number, code: string) =>
    api.post<void>(`${BASE}/me/passkeys/${id}/remove`, { code }),
} as const;

export const USER_ROLES: readonly UserRole[] = ["admin", "member"] as const;

export const USER_ROLE_LABELS: Record<UserRole, string> = {
  admin: "Admin",
  member: "Member",
};
