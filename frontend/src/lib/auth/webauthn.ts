/**
 * Turn a WebAuthn failure into something the UI can act on.
 *
 * `@simplewebauthn/browser` wraps DOMExceptions in a `WebAuthnError` with a
 * `code` and the original as `cause`; a raw DOMException can also reach us.
 * Both shapes are handled so the UI never has to know which it got.
 */

export type WebAuthnFailure = "cancelled" | "origin" | "unsupported" | "other";

export const ORIGIN_MISMATCH_MESSAGE =
  "This page's address does not match the app URL in Settings, so passkeys cannot be used here.";

function nameOf(err: unknown): string | undefined {
  if (!err || typeof err !== "object") return undefined;
  const cause = (err as { cause?: unknown }).cause;
  if (cause && typeof cause === "object" && "name" in cause) {
    return String((cause as { name: unknown }).name);
  }
  return "name" in err ? String((err as { name: unknown }).name) : undefined;
}

export function classifyWebAuthnError(err: unknown): WebAuthnFailure {
  const code = err && typeof err === "object" ? (err as { code?: unknown }).code : undefined;
  const name = nameOf(err);
  if (code === "ERROR_CEREMONY_ABORTED" || name === "NotAllowedError" || name === "AbortError") {
    return "cancelled";
  }
  if (
    code === "ERROR_INVALID_DOMAIN" ||
    code === "ERROR_INVALID_RP_ID" ||
    name === "SecurityError"
  ) {
    return "origin";
  }
  if (name === "NotSupportedError") return "unsupported";
  return "other";
}
