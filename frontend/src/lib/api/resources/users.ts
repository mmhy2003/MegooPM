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

const BASE = "/api/v1/users";

export const users = {
  list: () => api.get<User[]>(BASE),
  create: (body: UserCreate) => api.post<User>(BASE, body),
  update: (id: number, body: UserUpdate) => api.patch<User>(`${BASE}/${id}`, body),
  resetPassword: (id: number, body: PasswordReset) =>
    api.put<void>(`${BASE}/${id}/password`, body),
  remove: (id: number) => api.delete<void>(`${BASE}/${id}`),
  /** The caller's own profile (display name only). */
  updateMe: (body: ProfileUpdate) => api.patch<User>(`${BASE}/me`, body),
  /** The caller's own password; 400 when the current password is wrong. */
  changeMyPassword: (body: PasswordChange) => api.put<void>(`${BASE}/me/password`, body),
} as const;

export const USER_ROLES: readonly UserRole[] = ["admin", "member"] as const;

export const USER_ROLE_LABELS: Record<UserRole, string> = {
  admin: "Admin",
  member: "Member",
};
