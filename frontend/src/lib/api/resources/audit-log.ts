/**
 * Typed client for the audit log.
 *
 * One read-only endpoint, admin-only. Rows are append-only on the server, so
 * there is nothing here to create, change or delete — which is the point.
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type AuditLogEntry = Schemas["AuditLogRead"];
export type AuditLogPage = Schemas["AuditLogPage"];
export type AuditAction = Schemas["AuditAction"];

export interface AuditLogQuery {
  actor?: string;
  action?: AuditAction;
  objectType?: string;
  limit?: number;
  offset?: number;
}

const BASE = "/api/v1/audit-log";

function queryString(query: AuditLogQuery): string {
  const params = new URLSearchParams();
  // Empty strings are dropped, not sent: `?actor=` would be an empty filter
  // rather than no filter, and the API would match every row containing "".
  if (query.actor?.trim()) params.set("actor", query.actor.trim());
  if (query.action) params.set("action", query.action);
  if (query.objectType) params.set("object_type", query.objectType);
  if (query.limit != null) params.set("limit", String(query.limit));
  if (query.offset != null) params.set("offset", String(query.offset));
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

export const auditLog = {
  /** Newest first. `actor` matches part of an actor, ignoring case. */
  list: (query: AuditLogQuery = {}) => api.get<AuditLogPage>(`${BASE}${queryString(query)}`),
} as const;
