/**
 * Pure helpers shared by the Proxy Hosts / Upstreams UI.
 *
 * Kept free of React so the validation and error-surfacing logic — the parts
 * most worth pinning down — can be unit-tested in isolation.
 */
import { ApiError } from "@/lib/api";

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
