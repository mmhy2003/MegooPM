/**
 * Typed client for the stream (raw TCP/UDP forwarding) endpoints.
 *
 * A stream listens on `incoming_port` and forwards TCP and/or UDP traffic to a
 * backend `forward_host:forward_port`, optionally terminating TLS with
 * `certificate_id`. Shapes are derived from the generated OpenAPI schema.
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type Stream = Schemas["StreamRead"];
export type StreamCreate = Schemas["StreamCreate"];
export type StreamUpdate = Schemas["StreamUpdate"];

const BASE = "/api/v1/streams";

export const streams = {
  list: () => api.get<Stream[]>(BASE),
  get: (id: number) => api.get<Stream>(`${BASE}/${id}`),
  create: (body: StreamCreate) => api.post<Stream>(BASE, body),
  update: (id: number, body: StreamUpdate) => api.patch<Stream>(`${BASE}/${id}`, body),
  remove: (id: number) => api.delete<void>(`${BASE}/${id}`),
} as const;
