/**
 * Typed client for the custom-page endpoints.
 *
 * A custom page is a named, self-contained HTML document authored in the app;
 * images live inside it as base64 `data:` URIs rather than as separate assets.
 *
 * The index and the detail view differ deliberately: `list` returns
 * {@link CustomPageSummary} rows carrying a `size_bytes` instead of the source,
 * so rendering the table never pulls megabytes of embedded image data. Fetch
 * the document itself with `get`. Shapes are derived from the generated OpenAPI
 * schema (see {@link module:lib/api/types}).
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type CustomPage = Schemas["CustomPageRead"];
export type CustomPageSummary = Schemas["CustomPageSummary"];
export type CustomPageCreate = Schemas["CustomPageCreate"];
export type CustomPageUpdate = Schemas["CustomPageUpdate"];

const BASE = "/api/v1/custom-pages";

/** The API rejects a document larger than this; mirrors MAX_HTML_BYTES. */
export const MAX_PAGE_BYTES = 2 * 1024 * 1024;

export const customPages = {
  list: () => api.get<CustomPageSummary[]>(BASE),
  get: (id: number) => api.get<CustomPage>(`${BASE}/${id}`),
  create: (body: CustomPageCreate) => api.post<CustomPage>(BASE, body),
  update: (id: number, body: CustomPageUpdate) =>
    api.patch<CustomPage>(`${BASE}/${id}`, body),
  remove: (id: number) => api.delete<void>(`${BASE}/${id}`),
} as const;
