/**
 * Typed client for the redirection-host endpoints.
 *
 * A redirection host answers for a set of `domain_names` and issues an HTTP
 * redirect (`forward_http_code`) to `forward_domain_name` under
 * `forward_scheme`. Shapes are derived from the generated OpenAPI schema.
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type RedirectionHost = Schemas["RedirectionHostRead"];
export type RedirectionHostCreate = Schemas["RedirectionHostCreate"];
export type RedirectionHostUpdate = Schemas["RedirectionHostUpdate"];
export type RedirectScheme = Schemas["RedirectScheme"];

const BASE = "/api/v1/redirection-hosts";

export const redirectionHosts = {
  list: () => api.get<RedirectionHost[]>(BASE),
  get: (id: number) => api.get<RedirectionHost>(`${BASE}/${id}`),
  create: (body: RedirectionHostCreate) => api.post<RedirectionHost>(BASE, body),
  update: (id: number, body: RedirectionHostUpdate) =>
    api.patch<RedirectionHost>(`${BASE}/${id}`, body),
  remove: (id: number) => api.delete<void>(`${BASE}/${id}`),
} as const;

/** Target-scheme options; `auto` keeps the incoming request's scheme. */
export const REDIRECT_SCHEMES: readonly RedirectScheme[] = [
  "auto",
  "http",
  "https",
] as const;

/** Valid HTTP redirect status codes (300–308). */
export const REDIRECT_HTTP_CODES: readonly number[] = [
  300, 301, 302, 303, 307, 308,
] as const;

/** Human labels for the redirect status codes offered in the UI. */
export const REDIRECT_CODE_LABELS: Record<number, string> = {
  300: "300 Multiple Choices",
  301: "301 Moved Permanently",
  302: "302 Found (temporary)",
  303: "303 See Other",
  307: "307 Temporary Redirect",
  308: "308 Permanent Redirect",
};
