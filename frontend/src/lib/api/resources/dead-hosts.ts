/**
 * Typed client for the dead (404) host endpoints.
 *
 * A dead host answers for a set of `domain_names` and returns a 404 for every
 * request — useful for parking domains or explicitly swallowing traffic. It can
 * still terminate TLS via `certificate_id`. Shapes are derived from the
 * generated OpenAPI schema.
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type DeadHost = Schemas["DeadHostRead"];
export type DeadHostCreate = Schemas["DeadHostCreate"];
export type DeadHostUpdate = Schemas["DeadHostUpdate"];

const BASE = "/api/v1/dead-hosts";

export const deadHosts = {
  list: () => api.get<DeadHost[]>(BASE),
  get: (id: number) => api.get<DeadHost>(`${BASE}/${id}`),
  create: (body: DeadHostCreate) => api.post<DeadHost>(BASE, body),
  update: (id: number, body: DeadHostUpdate) => api.patch<DeadHost>(`${BASE}/${id}`, body),
  remove: (id: number) => api.delete<void>(`${BASE}/${id}`),
} as const;
