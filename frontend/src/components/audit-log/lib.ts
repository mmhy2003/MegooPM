import type { AuditAction } from "@/lib/api";

/** How long a summary may get before it starts deforming the table row. */
const MAX_SUMMARY = 120;

/** Whether an action reads as destructive, additive, or neither.
 *
 * Colour is what makes a delete findable while scrolling a page of updates.
 */
export function actionTone(action: AuditAction | string): "destructive" | "positive" | "neutral" {
  if (action === "delete" || action === "disable") return "destructive";
  if (action === "create" || action === "enable") return "positive";
  return "neutral";
}

/** "proxy_host" + 7 → "Proxy host #7". */
export function describeObject(objectType: string, objectId: number | null): string {
  const words = objectType.replace(/_/g, " ");
  const label = words.charAt(0).toUpperCase() + words.slice(1);
  // Settings rows carry no id, and "#null" is worse than nothing.
  return objectId == null ? label : `${label} #${objectId}`;
}

function renderValue(value: unknown): string {
  if (value === null || value === undefined) return "none";
  if (Array.isArray(value)) return value.map(renderValue).join(", ");
  if (typeof value === "object") return "{…}";
  return String(value);
}

/** `{full_name: [before, after]}` → `full_name: old → new`.
 *
 * The arrow lives here and nowhere else. A before/after pair is a two-item
 * list, and so is a host with two domains — indistinguishable by value. What
 * separates them is position: handlers always nest a diff under `changes`, so
 * a two-item list anywhere else is a genuine list. Guessing instead would print
 * "a.example.com → b.example.com" for a host that simply has two domains, which
 * in an audit log is not a cosmetic error.
 */
function renderChanges(changes: Record<string, unknown>): string {
  return Object.entries(changes)
    .map(([field, value]) =>
      Array.isArray(value) && value.length === 2
        ? `${field}: ${renderValue(value[0])} → ${renderValue(value[1])}`
        : `${field}: ${renderValue(value)}`,
    )
    .join(", ");
}

/**
 * One line describing what a row's `meta` holds.
 *
 * Deliberately lossy: nested objects collapse to `{…}` and long text is cut,
 * because the cell has to stay one line and the details dialog shows the whole
 * record anyway. Nothing here decides what is *worth* showing — every key the
 * row carries appears, in the order it was written, so a shape this function
 * has never seen still renders something true.
 */
export function summarise(meta: Record<string, unknown>): string {
  const parts = Object.entries(meta).map(([key, value]) =>
    key === "changes" && value !== null && typeof value === "object" && !Array.isArray(value)
      ? renderChanges(value as Record<string, unknown>)
      : `${key}: ${renderValue(value)}`,
  );
  if (parts.length === 0) return "—";
  const line = parts.join(", ");
  return line.length > MAX_SUMMARY ? `${line.slice(0, MAX_SUMMARY - 1)}…` : line;
}
