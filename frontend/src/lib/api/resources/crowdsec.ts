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

export const crowdsec = {
  /** Whether the LAPI is configured and reachable (never errors server-side). */
  health: () => api.get<CrowdSecHealth>(`${BASE}/health`),
  /** Active decisions the bouncer currently enforces. */
  listDecisions: () => api.get<DecisionList>(`${BASE}/decisions`),
  /** Push a manual operator decision (ban/captcha/throttle). */
  addDecision: (body: DecisionCreate) => api.post<Decision>(`${BASE}/decisions`, body),
  /** Lift a decision by its LAPI id. */
  deleteDecision: (id: number) =>
    api.delete<Record<string, number>>(`${BASE}/decisions/${id}`),
  /** Recent alerts, newest first. `limit` caps how many are returned. */
  listAlerts: (limit?: number) =>
    api.get<AlertList>(`${BASE}/alerts`, limit != null ? { query: { limit } } : undefined),
} as const;
