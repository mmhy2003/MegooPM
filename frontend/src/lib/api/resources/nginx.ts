/**
 * Typed client for the nginx endpoints (admin-only).
 *
 * `status` is how the last apply went — with nginx's own words when it failed.
 * `reload` re-runs the apply; the outcome arrives as an event, and the status
 * reflects it once it has.
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type NginxApplyStatus = Schemas["NginxApplyStatus"];

const BASE = "/api/v1/nginx";

export const nginx = {
  status: () => api.get<NginxApplyStatus>(`${BASE}/status`),
  reload: () => api.post<Schemas["TaskEnqueued"]>(`${BASE}/reload`, {}),
} as const;
