/**
 * Typed client for the proxy-host endpoints.
 *
 * A proxy host terminates a set of domain names and forwards matching traffic
 * to an upstream pool (`upstream_id`). Shapes are derived from the generated
 * OpenAPI schema (see {@link module:lib/api/types}).
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type ProxyHost = Schemas["ProxyHostRead"];
export type ProxyHostCreate = Schemas["ProxyHostCreate"];
export type ProxyHostUpdate = Schemas["ProxyHostUpdate"];
export type HttpScheme = Schemas["HttpScheme"];

const BASE = "/api/v1/proxy-hosts";

export const proxyHosts = {
  list: () => api.get<ProxyHost[]>(BASE),
  get: (id: number) => api.get<ProxyHost>(`${BASE}/${id}`),
  create: (body: ProxyHostCreate) => api.post<ProxyHost>(BASE, body),
  update: (id: number, body: ProxyHostUpdate) => api.patch<ProxyHost>(`${BASE}/${id}`, body),
  remove: (id: number) => api.delete<void>(`${BASE}/${id}`),
} as const;

export const HTTP_SCHEMES: readonly HttpScheme[] = ["http", "https"] as const;
