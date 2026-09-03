import type { CrowdSecJobRun } from "@/lib/api";

export const WEEKDAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
] as const;

/** The browser's offset from UTC in whole hours at `now` (DST-aware). */
function offsetHours(now: Date): number {
  return Math.round(-now.getTimezoneOffset() / 60);
}

export function utcHourToLocal(hourUtc: number, now: Date = new Date()): number {
  return (((hourUtc + offsetHours(now)) % 24) + 24) % 24;
}

export function localHourToUtc(hourLocal: number, now: Date = new Date()): number {
  return (((hourLocal - offsetHours(now)) % 24) + 24) % 24;
}

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** One sentence for the Hub card's status line. */
export function describeHubRun(run: CrowdSecJobRun | null): string {
  if (!run) return "Never run.";
  if (!run.finished_at) return `Running since ${when(run.started_at)}…`;
  if (!run.ok) return `Last run ${when(run.started_at)} failed: ${run.error ?? "unknown error"}`;
  const updated = Array.isArray(run.detail.updated) ? (run.detail.updated as string[]) : [];
  if (updated.length === 0) return `Last run ${when(run.started_at)}: no changes.`;
  const n = updated.length;
  return `Last run ${when(run.started_at)}: ${n} item${n === 1 ? "" : "s"} updated${
    run.restarted ? ", CrowdSec restarted" : ""
  }.`;
}

/** The Blocklist card's state: desired vs achieved. */
export function describeCapiRun(
  desired: boolean,
  run: CrowdSecJobRun | null,
  running: boolean,
): { label: string; failed: boolean } {
  if (running || (run && !run.finished_at)) {
    return { label: desired ? "Turning on…" : "Turning off…", failed: false };
  }
  if (run && !run.ok) {
    return {
      label: `Failed: ${run.error ?? "unknown error"} — the previous configuration was restored.`,
      failed: true,
    };
  }
  const achieved = run ? run.detail.enabled === true : false;
  if (desired === achieved) return { label: desired ? "On" : "Off", failed: false };
  // Desired but never applied (e.g. saved while reloads were unconfigured).
  return { label: desired ? "Off — not applied yet" : "On — not applied yet", failed: false };
}
