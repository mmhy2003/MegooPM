/**
 * Typed client for the CrowdSec security endpoints.
 *
 * CrowdSec is the intrusion-prevention layer: the LAPI holds *decisions*
 * (active bans/captcha/throttle the bouncer enforces) and *alerts* (scenarios
 * that fired). Operators can push a manual decision (ban an IP/range) or lift
 * one by id; both mutations are admin-only and recorded in the audit log.
 *
 * Shapes are derived from the generated OpenAPI schema (see
 * {@link module:lib/api/types}) — never hand-authored.
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type CrowdSecHealth = Schemas["CrowdSecHealth"];
export type Decision = Schemas["Decision"];
export type DecisionCreate = Schemas["DecisionCreate"];
export type DecisionList = Schemas["DecisionList"];
export type Alert = Schemas["Alert"];
export type AlertList = Schemas["AlertList"];
export type AlertSource = Schemas["AlertSource"];

/** The scope a manual decision applies to — a single IP or a CIDR range. */
export type DecisionScope = DecisionCreate["scope"];
/** The remediation a manual decision enforces. */
export type DecisionType = DecisionCreate["type"];

export const DECISION_SCOPES: readonly DecisionScope[] = ["Ip", "Range"] as const;
export const DECISION_TYPES: readonly DecisionType[] = [
  "ban",
  "captcha",
  "throttle",
] as const;

export const DECISION_SCOPE_LABELS: Record<DecisionScope, string> = {
  Ip: "IP address",
  Range: "CIDR range",
};

export const DECISION_TYPE_LABELS: Record<DecisionType, string> = {
  ban: "Ban",
  captcha: "Captcha",
  throttle: "Throttle",
};

/** Common ban durations offered in the manual-decision form. */
export const DECISION_DURATIONS: readonly { value: string; label: string }[] = [
  { value: "1h", label: "1 hour" },
  { value: "4h", label: "4 hours" },
  { value: "24h", label: "1 day" },
  { value: "168h", label: "1 week" },
  { value: "720h", label: "30 days" },
] as const;

const BASE = "/api/v1/crowdsec";

/**
 * Shared query for the two paginated list endpoints (MEG-43 contract).
 *
 * `page` is 1-based; `pageSize` is capped at 200 server-side.
 * `includeCommunity` widens the result to community/CAPI/blocklist origins —
 * it defaults to `false` server-side, so the default view is local/manual/AppSec
 * records only. camelCase here is mapped to the API's snake_case params.
 */
export interface ListParams {
  page?: number;
  pageSize?: number;
  includeCommunity?: boolean;
}

/** Default records per page; matches the backend default. */
export const DEFAULT_PAGE_SIZE = 50;

/** Page sizes offered in the pagination controls. */
export const PAGE_SIZE_OPTIONS: readonly number[] = [10, 25, 50, 100] as const;

function listQuery(params?: ListParams): Record<string, number | boolean> {
  const query: Record<string, number | boolean> = {};
  if (params?.page != null) query.page = params.page;
  if (params?.pageSize != null) query.page_size = params.pageSize;
  if (params?.includeCommunity != null) query.include_community = params.includeCommunity;
  return query;
}

export const crowdsec = {
  /** Whether the LAPI is configured and reachable (never errors server-side). */
  health: () => api.get<CrowdSecHealth>(`${BASE}/health`),
  /** A page of active decisions the bouncer currently enforces. */
  listDecisions: (params?: ListParams) =>
    api.get<DecisionList>(`${BASE}/decisions`, { query: listQuery(params) }),
  /** Push a manual operator decision (ban/captcha/throttle). */
  addDecision: (body: DecisionCreate) => api.post<Decision>(`${BASE}/decisions`, body),
  /** Lift a decision by its LAPI id. */
  deleteDecision: (id: number) =>
    api.delete<Record<string, number>>(`${BASE}/decisions/${id}`),
  /** A page of recent alerts, newest first. */
  listAlerts: (params?: ListParams) =>
    api.get<AlertList>(`${BASE}/alerts`, { query: listQuery(params) }),
} as const;
