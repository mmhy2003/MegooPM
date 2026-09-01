/**
 * Typed client for the instance-settings endpoint.
 *
 * One settings row exists, so the path carries no id. `update` requires
 * `default_site_mode`: coherence ("redirect needs a URL") cannot be checked
 * against a payload that omits the mode, so the API asks for the whole
 * default-site group at once — which is also how the UI's single Save works.
 * Shapes come from the generated OpenAPI schema.
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type InstanceSettings = Schemas["InstanceSettingsRead"];
export type InstanceSettingsUpdate = Schemas["InstanceSettingsUpdate"];
export type DefaultSiteMode = Schemas["DefaultSiteMode"];

const BASE = "/api/v1/settings";

export const instanceSettings = {
  get: () => api.get<InstanceSettings>(BASE),
  update: (body: InstanceSettingsUpdate) => api.patch<InstanceSettings>(BASE, body),
} as const;
