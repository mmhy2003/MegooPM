/**
 * Typed surface over the generated OpenAPI schema.
 *
 * `generated/schema.ts` is produced by `npm run gen:api` from the backend's
 * published `openapi.json` — it is generated output, never hand-edit it.
 * Feature code should derive request/response shapes from here (or extend this
 * module) instead of hand-authoring types that can silently drift from the
 * backend contract.
 */
import type { components, operations, paths } from "@/lib/api/generated/schema";

/** All named response/request schemas from the backend (`components.schemas`). */
export type Schemas = components["schemas"];

/** The full set of API paths and their operations. */
export type ApiPaths = paths;

/** All named operations, keyed by operationId. */
export type ApiOperations = operations;
