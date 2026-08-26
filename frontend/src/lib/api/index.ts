/**
 * Public entrypoint for the MegooPM API client.
 *
 * Feature tickets add resource modules under `src/lib/api/resources/` and
 * re-export them here so callers import from a single, stable path:
 * `import { api, apiFetch } from "@/lib/api"`.
 */
export { api, apiFetch, setAuthTokenProvider } from "@/lib/api/client";
export type { ApiRequestOptions, QueryValue } from "@/lib/api/client";
export { ApiError } from "@/lib/api/errors";
