/**
 * Pure helpers shared by the Access Lists UI.
 *
 * Kept free of React so the label / normalization logic stays unit-testable.
 * Error surfacing is delegated to the shared {@link describeError} so the two
 * FastAPI error shapes (409 duplicate username, 422 invalid IP/CIDR) render the
 * same way they do across the rest of the app.
 */
export { describeError } from "@/components/proxy-hosts/lib";

/** The word shown for `satisfy_any` — "Any" gate vs. "All" gates required. */
export function satisfyLabel(satisfyAny: boolean): "Any" | "All" {
  return satisfyAny ? "Any" : "All";
}

/**
 * One-line summary of how the gates combine, for tooltips / helper text.
 */
export function satisfyDescription(satisfyAny: boolean): string {
  return satisfyAny
    ? "A request passes if it satisfies EITHER basic-auth OR an allow rule."
    : "A request must satisfy BOTH basic-auth AND the IP rules.";
}

/**
 * Trim a client-rule address for submission. `all` is normalized to lower-case;
 * everything else is left as-typed so IPv6 zone ids etc. survive — the backend
 * is the authority on validity and returns 422 for anything malformed.
 */
export function normalizeAddress(input: string): string {
  const trimmed = input.trim();
  return trimmed.toLowerCase() === "all" ? "all" : trimmed;
}
