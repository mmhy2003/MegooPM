/**
 * Pure helpers shared by the Proxy Hosts / Upstreams UI.
 *
 * Kept free of React so the validation and error-surfacing logic — the parts
 * most worth pinning down — can be unit-tested in isolation.
 */
import { ApiError, type HttpScheme, type ProxyHost, type ProxyHostCreate } from "@/lib/api";

/** A single FastAPI 422 validation item (`{loc, msg, type}`). */
interface ValidationItem {
  loc?: (string | number)[];
  msg?: string;
}

export interface DescribedError {
  /** A human-readable, single-line summary suitable for a toast/alert. */
  message: string;
  /** Field-scoped messages keyed by the offending field name, when derivable. */
  fieldErrors: Record<string, string>;
}

function isValidationList(detail: unknown): detail is ValidationItem[] {
  return (
    Array.isArray(detail) &&
    detail.every((d) => d && typeof d === "object" && "msg" in d)
  );
}

/**
 * Normalize any thrown value into a message + per-field errors.
 *
 * Handles FastAPI's two error shapes: a plain `{detail: string}` (raised via
 * `HTTPException`) and the 422 `{detail: [{loc, msg}, …]}` validation list.
 */
export function describeError(err: unknown): DescribedError {
  if (err instanceof ApiError) {
    const detail = (err.body as { detail?: unknown } | null)?.detail;
    if (isValidationList(detail)) {
      const fieldErrors: Record<string, string> = {};
      for (const item of detail) {
        // loc is like ["body", "domain_names", 0] — take the first field-ish part.
        const field = item.loc?.find(
          (part) => typeof part === "string" && part !== "body",
        );
        if (typeof field === "string" && item.msg && !fieldErrors[field]) {
          fieldErrors[field] = item.msg;
        }
      }
      const first = Object.values(fieldErrors)[0];
      return {
        message: first ?? "Please fix the highlighted fields.",
        fieldErrors,
      };
    }
    return { message: err.detail, fieldErrors: {} };
  }
  if (err instanceof Error) {
    return { message: err.message, fieldErrors: {} };
  }
  return { message: "Something went wrong. Please try again.", fieldErrors: {} };
}

// Domain parsing now lives with the shared tag input; re-exported so existing
// imports from this module keep working.
export { parseDomains } from "@/components/domains/lib";

// --- Proxy host dialog form model ------------------------------------------

/** Sentinel Select values for "nothing attached" (`null` on the wire). */
export const NO_ACCESS_LIST = "none";
export const NO_CERTIFICATE = "none";

export type DialogTab = "forwarding" | "certificate" | "advanced";

export const TOGGLE_KEYS = [
  "ssl_forced",
  "http2_support",
  "hsts_enabled",
  "hsts_subdomains",
  "caching_enabled",
  "block_exploits",
  "allow_websocket_upgrade",
  "crowdsec_enabled",
] as const;
export type ToggleKey = (typeof TOGGLE_KEYS)[number];

export interface LocationRow {
  /** Stable React key; `loc-<id>` for stored rows, `loc-new-<n>` for new ones. */
  key: string;
  path: string;
  /** Pool id as a Select value; "" while unset. */
  upstreamId: string;
  scheme: HttpScheme;
}

export interface ProxyHostFormState {
  domains: string[];
  accessListId: string;
  enabled: boolean;
  rootUpstreamId: string;
  rootScheme: HttpScheme;
  locations: LocationRow[];
  certificateId: string;
  toggles: Record<ToggleKey, boolean>;
  advancedConfig: string;
}

/** A validation failure and the tab that holds the offending field (`null` = outside tabs). */
export interface FormError {
  message: string;
  tab: DialogTab | null;
}

let newRowSeq = 0;

export function newLocationRow(): LocationRow {
  newRowSeq += 1;
  return { key: `loc-new-${newRowSeq}`, path: "", upstreamId: "", scheme: "http" };
}

export function emptyToggles(): Record<ToggleKey, boolean> {
  return Object.fromEntries(TOGGLE_KEYS.map((k) => [k, false])) as Record<ToggleKey, boolean>;
}

export function stateFromHost(host: ProxyHost | null | undefined): ProxyHostFormState {
  if (!host) {
    return {
      domains: [],
      accessListId: NO_ACCESS_LIST,
      enabled: true,
      rootUpstreamId: "",
      rootScheme: "http",
      locations: [],
      certificateId: NO_CERTIFICATE,
      toggles: emptyToggles(),
      advancedConfig: "",
    };
  }
  return {
    domains: [...host.domain_names],
    accessListId: host.access_list_id ? String(host.access_list_id) : NO_ACCESS_LIST,
    enabled: host.enabled ?? true,
    rootUpstreamId: String(host.upstream_id),
    rootScheme: host.forward_scheme ?? "http",
    locations: (host.locations ?? []).map((l) => ({
      key: `loc-${l.id}`,
      path: l.path,
      upstreamId: String(l.upstream_id),
      scheme: l.forward_scheme ?? "http",
    })),
    certificateId: host.certificate_id ? String(host.certificate_id) : NO_CERTIFICATE,
    toggles: Object.fromEntries(TOGGLE_KEYS.map((k) => [k, host[k] ?? false])) as Record<
      ToggleKey,
      boolean
    >,
    advancedConfig: host.advanced_config ?? "",
  };
}

const LOCATION_FORBIDDEN = /[\s{};"]/;

/** Mirrors the backend path rules so mistakes are caught before the request. */
export function validateLocations(rows: LocationRow[]): FormError | null {
  const seen = new Set<string>();
  for (const row of rows) {
    const path = row.path.trim();
    let message: string | null = null;
    if (!path.startsWith("/")) message = `Location path "${path}" must start with /.`;
    else if (path === "/") message = "/ is the root route — add a sub-path such as /api/.";
    else if (LOCATION_FORBIDDEN.test(path))
      message = `Location path "${path}" must not contain whitespace or { } ; ".`;
    else if (path.length > 255) message = "Location paths are limited to 255 characters.";
    else if (seen.has(path)) message = `Duplicate location path "${path}".`;
    else if (!row.upstreamId) message = `Select an upstream pool for ${path}.`;
    if (message) return { message, tab: "forwarding" };
    seen.add(path);
  }
  return null;
}

export function validateForm(form: ProxyHostFormState): FormError | null {
  if (form.domains.length === 0) return { message: "Enter at least one domain name.", tab: null };
  if (!form.rootUpstreamId)
    return { message: "Select an upstream pool to forward to.", tab: "forwarding" };
  return validateLocations(form.locations);
}

function idOrNull(value: string, sentinel: string): number | null {
  return value === sentinel ? null : Number.parseInt(value, 10);
}

export function buildPayload(
  form: ProxyHostFormState,
  host: ProxyHost | null | undefined,
): ProxyHostCreate {
  return {
    domain_names: form.domains,
    upstream_id: Number.parseInt(form.rootUpstreamId, 10),
    forward_scheme: form.rootScheme,
    certificate_id: idOrNull(form.certificateId, NO_CERTIFICATE),
    access_list_id: idOrNull(form.accessListId, NO_ACCESS_LIST),
    enabled: form.enabled,
    advanced_config: form.advancedConfig,
    ...form.toggles,
    locations: form.locations.map((row) => ({
      path: row.path.trim(),
      upstream_id: Number.parseInt(row.upstreamId, 10),
      forward_scheme: row.scheme,
    })),
    // `crowdsec_enabled` is a form toggle (Advanced tab). AppSec is not
    // per-host yet (docs/crowdsec.md), so its flag passes through untouched.
    crowdsec_appsec_enabled: host?.crowdsec_appsec_enabled ?? false,
  };
}
