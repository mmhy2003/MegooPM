/**
 * Typed client for the upstream-pool + backend endpoints.
 *
 * A pool (`Upstream`) is a load-balanced set of backend servers a proxy host
 * forwards to — MegooPM's headline capability over stock NPM. Shapes are derived
 * from the generated OpenAPI schema so they cannot silently drift from the
 * backend contract (see {@link module:lib/api/types}).
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type Upstream = Schemas["UpstreamRead"];
export type UpstreamCreate = Schemas["UpstreamCreate"];
export type UpstreamUpdate = Schemas["UpstreamUpdate"];
export type Backend = Schemas["BackendRead"];
export type BackendCreate = Schemas["BackendCreate"];
export type BackendUpdate = Schemas["BackendUpdate"];
export type LoadBalanceMethod = Schemas["LoadBalanceMethod"];

const BASE = "/api/v1/upstreams";

export const upstreams = {
  list: () => api.get<Upstream[]>(BASE),
  get: (id: number) => api.get<Upstream>(`${BASE}/${id}`),
  create: (body: UpstreamCreate) => api.post<Upstream>(BASE, body),
  update: (id: number, body: UpstreamUpdate) => api.patch<Upstream>(`${BASE}/${id}`, body),
  remove: (id: number) => api.delete<void>(`${BASE}/${id}`),

  addBackend: (upstreamId: number, body: BackendCreate) =>
    api.post<Backend>(`${BASE}/${upstreamId}/backends`, body),
  updateBackend: (upstreamId: number, backendId: number, body: BackendUpdate) =>
    api.patch<Backend>(`${BASE}/${upstreamId}/backends/${backendId}`, body),
  removeBackend: (upstreamId: number, backendId: number) =>
    api.delete<void>(`${BASE}/${upstreamId}/backends/${backendId}`),
} as const;

/** All nginx load-balancing strategies, in display order. */
export const LB_METHODS: readonly LoadBalanceMethod[] = [
  "round_robin",
  "least_conn",
  "ip_hash",
  "hash",
  "random",
] as const;

/** Human-readable labels for the load-balancing strategies. */
export const LB_METHOD_LABELS: Record<LoadBalanceMethod, string> = {
  round_robin: "Round robin",
  least_conn: "Least connections",
  ip_hash: "IP hash",
  hash: "Hash",
  random: "Random",
};
