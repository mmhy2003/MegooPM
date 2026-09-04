"use client";

import { useMemo } from "react";
import { Activity, ShieldBan, TriangleAlert } from "lucide-react";

import type { Alert, Decision } from "@/lib/api";
import { buildTimeline, topOffenders, type TimeBucket } from "@/components/security/lib";
import { CountryFlag } from "@/components/ui/country-flag";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const WINDOW_BUCKETS = 12;
const BUCKET_MS = 2 * 60 * 60 * 1000; // 2 hours → a rolling 24h window

function StatTile({
  icon: Icon,
  label,
  value,
  tone = "default",
}: {
  icon: typeof Activity;
  label: string;
  value: string | number;
  tone?: "default" | "success" | "warning" | "destructive";
}) {
  const toneClass =
    tone === "success"
      ? "text-success"
      : tone === "warning"
        ? "text-warning"
        : tone === "destructive"
          ? "text-destructive"
          : "text-foreground";
  return (
    <Card>
      <CardContent className="flex items-center gap-3 py-4">
        <div className="flex size-9 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <Icon className="size-4" />
        </div>
        <div>
          <p className="text-xs text-muted-foreground">{label}</p>
          <p className={`text-2xl font-semibold tabular-nums ${toneClass}`}>{value}</p>
        </div>
      </CardContent>
    </Card>
  );
}

function bucketLabel(bucket: TimeBucket): string {
  const d = new Date(bucket.startMs);
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/** Column chart of alert volume over the last 24h (single-hue magnitude). */
function AlertsTimeline({ alerts, nowMs }: { alerts: Alert[]; nowMs: number }) {
  const buckets = useMemo(
    () => buildTimeline(alerts, { nowMs, bucketMs: BUCKET_MS, buckets: WINDOW_BUCKETS }),
    [alerts, nowMs],
  );
  const max = Math.max(1, ...buckets.map((b) => b.count));
  const total = buckets.reduce((sum, b) => sum + b.count, 0);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Alerts over time</CardTitle>
        <CardDescription>Last 24 hours, in 2-hour buckets.</CardDescription>
      </CardHeader>
      <CardContent>
        {total === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No alerts in the last 24 hours.
          </p>
        ) : (
          <div
            role="img"
            aria-label={`${total} alerts over the last 24 hours`}
            className="flex h-32 items-end gap-1.5"
          >
            {buckets.map((b) => (
              <div
                key={b.startMs}
                className="flex flex-1 flex-col items-center justify-end gap-1"
                title={`${bucketLabel(b)} — ${b.count} alert${b.count === 1 ? "" : "s"}`}
              >
                <div
                  className="w-full rounded-t bg-chart-1"
                  style={{ height: `${(b.count / max) * 100}%`, minHeight: b.count > 0 ? 4 : 0 }}
                />
              </div>
            ))}
          </div>
        )}
        {total > 0 ? (
          <div className="mt-1.5 flex justify-between text-[11px] text-muted-foreground">
            <span>{bucketLabel(buckets[0])}</span>
            <span>now</span>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

/** Horizontal bars ranking the noisiest sources by event count. */
function TopOffenders({ alerts }: { alerts: Alert[] }) {
  const offenders = useMemo(() => topOffenders(alerts, 5), [alerts]);
  const max = Math.max(1, ...offenders.map((o) => o.count));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Top offenders</CardTitle>
        <CardDescription>Sources with the most alert events.</CardDescription>
      </CardHeader>
      <CardContent>
        {offenders.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No source data in recent alerts.
          </p>
        ) : (
          <ul className="space-y-2.5">
            {offenders.map((o) => (
              <li key={o.key} className="flex items-center gap-3">
                <span
                  className="flex w-36 shrink-0 items-center gap-1.5 truncate font-mono text-xs"
                  title={o.key}
                >
                  <CountryFlag country={o.country} />
                  {o.key}
                </span>
                <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-chart-1"
                    style={{ width: `${Math.max((o.count / max) * 100, 4)}%` }}
                  />
                </div>
                <span className="w-10 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                  {o.count}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

export function SecurityMetrics({
  decisions,
  alerts,
  decisionsTotal,
  alertsTotal,
  nowMs,
}: {
  /** The current page of decisions (drives the visualizations). */
  decisions: Decision[];
  /** The current page of alerts (drives the timeline/offenders). */
  alerts: Alert[];
  /** Total decisions matching the active filter, across all pages. */
  decisionsTotal?: number;
  /** Total alerts matching the active filter, across all pages. */
  alertsTotal?: number;
  nowMs: number;
}) {
  // Count tiles reflect the server-side totals for the active filter; the
  // charts below stay on the current page (we never fetch every record).
  const decisionCount = decisionsTotal ?? decisions.length;
  const alertCount = alertsTotal ?? alerts.length;
  const bans = decisions.filter((d) => d.type === "ban").length;

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <StatTile icon={ShieldBan} label="Active decisions" value={decisionCount} />
        <StatTile
          icon={Activity}
          label="Bans on this page"
          value={bans}
          tone={bans > 0 ? "destructive" : "default"}
        />
        <StatTile icon={TriangleAlert} label="Recent alerts" value={alertCount} />
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <AlertsTimeline alerts={alerts} nowMs={nowMs} />
        <TopOffenders alerts={alerts} />
      </div>
    </div>
  );
}
