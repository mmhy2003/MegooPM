/**
 * Typed client for the access-list endpoints.
 *
 * An access list is an authorization gate attached to a proxy host: it combines
 * HTTP basic-auth users with allow/deny IP rules. `satisfy_any` decides whether
 * a request must clear BOTH gates (false) or EITHER one (true). Passwords are
 * write-only — accepted on create/reset, never returned. Shapes are derived
 * from the generated OpenAPI schema (see {@link module:lib/api/types}).
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type AccessList = Schemas["AccessListRead"];
export type AccessListCreate = Schemas["AccessListCreate"];
export type AccessListUpdate = Schemas["AccessListUpdate"];
export type AccessListAuthUser = Schemas["AccessListAuthRead"];
export type AccessListAuthCreate = Schemas["AccessListAuthCreate"];
export type AccessListAuthUpdate = Schemas["AccessListAuthUpdate"];
export type AccessListClientRule = Schemas["AccessListClientRead"];
export type AccessListClientCreate = Schemas["AccessListClientCreate"];
export type AccessListClientUpdate = Schemas["AccessListClientUpdate"];
export type AccessListDirective = Schemas["AccessListDirective"];

const BASE = "/api/v1/access-lists";

export const accessLists = {
  list: () => api.get<AccessList[]>(BASE),
  get: (id: number) => api.get<AccessList>(`${BASE}/${id}`),
  create: (body: AccessListCreate) => api.post<AccessList>(BASE, body),
  update: (id: number, body: AccessListUpdate) => api.patch<AccessList>(`${BASE}/${id}`, body),
  remove: (id: number) => api.delete<void>(`${BASE}/${id}`),

  /** Basic-auth users guarding the list. Passwords are write-only. */
  authUsers: {
    add: (listId: number, body: AccessListAuthCreate) =>
      api.post<AccessListAuthUser>(`${BASE}/${listId}/auth-users`, body),
    /** Reset an existing user's password (the only mutable field). */
    resetPassword: (listId: number, userId: number, body: AccessListAuthUpdate) =>
      api.patch<AccessListAuthUser>(`${BASE}/${listId}/auth-users/${userId}`, body),
    remove: (listId: number, userId: number) =>
      api.delete<void>(`${BASE}/${listId}/auth-users/${userId}`),
  },

  /** Allow/deny rules for IP addresses, CIDR ranges, or `all`. */
  clients: {
    add: (listId: number, body: AccessListClientCreate) =>
      api.post<AccessListClientRule>(`${BASE}/${listId}/clients`, body),
    update: (listId: number, ruleId: number, body: AccessListClientUpdate) =>
      api.patch<AccessListClientRule>(`${BASE}/${listId}/clients/${ruleId}`, body),
    remove: (listId: number, ruleId: number) =>
      api.delete<void>(`${BASE}/${listId}/clients/${ruleId}`),
  },
} as const;

export const ACCESS_LIST_DIRECTIVES: readonly AccessListDirective[] = ["allow", "deny"] as const;

/** Human labels for the client-rule directive enum. */
export const DIRECTIVE_LABELS: Record<AccessListDirective, string> = {
  allow: "Allow",
  deny: "Deny",
};
