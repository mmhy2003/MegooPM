/**
 * Typed client for the dashboard endpoints.
 *
 * Two calls, not one, mirroring the API: the summary is local-database work and
 * always answers, while the threat list depends on CrowdSec. Keeping them apart
 * means a CrowdSec outage empties the map rather than the whole page.
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type DashboardSummary = Schemas["DashboardSummary"];
export type CertificateHealth = Schemas["CertificateHealth"];
export type ConfigHealth = Schemas["ConfigHealth"];
export type InventoryCounts = Schemas["InventoryCounts"];
export type SecuritySummary = Schemas["SecuritySummary"];
export type TrafficSummary = Schemas["TrafficSummary"];
export type ThreatPoint = Schemas["ThreatPoint"];

const BASE = "/api/v1/dashboard";

export const dashboard = {
  summary: () => api.get<DashboardSummary>(`${BASE}/summary`),
  threats: () => api.get<ThreatPoint[]>(`${BASE}/threats`),
} as const;
