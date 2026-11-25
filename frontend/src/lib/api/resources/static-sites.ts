/**
 * Typed client for the static-site endpoints.
 *
 * A static site answers for a set of `domain_names` by serving the files in
 * `folder`, a sub-folder of the read-only sites folder mounted at /sites.
 * `folders()` lists what is there. Shapes are derived from the generated
 * OpenAPI schema.
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type StaticSite = Schemas["StaticSiteRead"];
export type StaticSiteCreate = Schemas["StaticSiteCreate"];
export type StaticSiteUpdate = Schemas["StaticSiteUpdate"];
export type StaticSiteFolders = Schemas["FoldersResult"];
export type StaticSiteFolder = Schemas["FolderEntry"];
export type FolderStatus = StaticSite["folder_status"];

const BASE = "/api/v1/static-sites";

export const staticSites = {
  list: () => api.get<StaticSite[]>(BASE),
  get: (id: number) => api.get<StaticSite>(`${BASE}/${id}`),
  create: (body: StaticSiteCreate) => api.post<StaticSite>(BASE, body),
  update: (id: number, body: StaticSiteUpdate) => api.patch<StaticSite>(`${BASE}/${id}`, body),
  remove: (id: number) => api.delete<void>(`${BASE}/${id}`),
  folders: () => api.get<StaticSiteFolders>(`${BASE}/folders`),
} as const;
