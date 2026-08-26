/**
 * Pure helpers for the Security (CrowdSec) dashboard.
 *
 * Kept free of React so the metric aggregation and time formatting — the parts
 * most worth pinning down — can be unit-tested in isolation. `nowMs` is always
 * injected rather than read from the clock so the results are deterministic.
 */
import type { Alert, Decision } from "@/lib/api";

// Re-exported so security components surface API errors identically to the rest
// of the app without each importing from the proxy-hosts module.
export { describeError } from "@/components/proxy-hosts/lib";

/** Parse an ISO timestamp to epoch ms, or `null` when absent/unparseable. */
export function parseTimestamp(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : ms;
}

/** Compact "5m ago" style relative time; `nowMs` is injected for determinism. */
export function formatRelativeTime(
  iso: string | null | undefined,
  nowMs: number,
): string {
  const t = parseTimestamp(iso);
  if (t == null) return "—";
  const diff = nowMs - t;
  if (diff < 60_000) return "just now";
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export interface TimeBucket {
  /** Epoch ms of the bucket's start (inclusive lower bound). */
  startMs: number;
  /** Number of alerts whose start falls in this bucket. */
  count: number;
}

/**
 * Bucket alerts into a fixed-width timeline ending at `nowMs`.
 *
 * Returns exactly `buckets` entries, oldest first. Each bucket spans
 * `bucketMs`; the last covers `(nowMs - bucketMs, nowMs]`. Alerts older than
 * the window are dropped; the rare future-dated alert (clock skew) is clamped
 * into the newest bucket.
 */
export function buildTimeline(
  alerts: Alert[],
  opts: { nowMs: number; bucketMs: number; buckets: number },
): TimeBucket[] {
  const { nowMs, bucketMs, buckets } = opts;
  const result: TimeBucket[] = Array.from({ length: buckets }, (_, i) => ({
    startMs: nowMs - (buckets - i) * bucketMs,
    count: 0,
  }));
  for (const alert of alerts) {
    const t = parseTimestamp(alert.start_at ?? alert.created_at);
    if (t == null) continue;
    let fromNow = Math.floor((nowMs - t) / bucketMs);
    if (fromNow < 0) fromNow = 0; // future-dated → newest bucket
    if (fromNow >= buckets) continue; // older than the window
    result[buckets - 1 - fromNow].count += 1;
  }
  return result;
}

export interface Offender {
  /** Source IP or range that raised the alerts. */
  key: string;
  /** Total events attributed to this source across recent alerts. */
  count: number;
}

/** Best available identifier for an alert's source. */
export function alertSourceKey(alert: Alert): string | null {
  const source = alert.source;
  return source?.value ?? source?.ip ?? null;
}

/**
 * Rank the noisiest sources across recent alerts by total event count,
 * returning at most `limit` offenders (highest first).
 */
export function topOffenders(alerts: Alert[], limit = 5): Offender[] {
  const totals = new Map<string, number>();
  for (const alert of alerts) {
    const key = alertSourceKey(alert);
    if (!key) continue;
    const events = alert.events_count ?? 1;
    totals.set(key, (totals.get(key) ?? 0) + (events > 0 ? events : 1));
  }
  return [...totals.entries()]
    .map(([key, count]) => ({ key, count }))
    .sort((a, b) => b.count - a.count || a.key.localeCompare(b.key))
    .slice(0, limit);
}

/** Stable React key for a decision row (LAPI id is optional). */
export function decisionRowKey(decision: Decision, index: number): string {
  return decision.id != null
    ? `id-${decision.id}`
    : `${decision.scope}:${decision.value}:${index}`;
}
