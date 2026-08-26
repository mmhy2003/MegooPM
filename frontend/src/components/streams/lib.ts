/**
 * Pure helpers for the Streams UI, kept React-free so the port-validation
 * logic — the part most worth pinning down — can be unit-tested in isolation.
 */

/** Parse a port string into a valid 1–65535 integer, or `null` if invalid. */
export function parsePort(input: string): number | null {
  const trimmed = input.trim();
  if (!/^\d+$/.test(trimmed)) return null;
  const n = Number.parseInt(trimmed, 10);
  if (!Number.isInteger(n) || n < 1 || n > 65535) return null;
  return n;
}
