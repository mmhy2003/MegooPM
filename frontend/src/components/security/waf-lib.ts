import type { AppSecBodyAction, CrowdSecWafStatus } from "@/lib/api";

/** The API's bounds, in whole megabytes (CrowdSecWafUpdate.max_body_mb). The
 *  ceiling sits below the 128 MiB that Coraza, under CrowdSec's WAF, rejects
 *  on its own whatever the action. */
export const MIN_MB = 1;
export const MAX_MB = 100;

/** What every install starts with (migration defaults and the seeded file). */
export const DEFAULT_MB = 10;
export const DEFAULT_ACTION: AppSecBodyAction = "partial";

/** Above CrowdSec's default the card notes the memory cost. Measured on
 *  CrowdSec 1.8.1 with one 600 MB upload: ~40 MB at 10 MB, ~800 MB at 100 MB,
 *  ~1.3 GB at 1 GB — several times the limit, not the limit itself. */
export const MEMORY_NOTE_MB = DEFAULT_MB;

/** Whole MB for the API, or null for anything it would refuse. */
export function toMb(value: string): number | null {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  const mb = Number(trimmed);
  if (!Number.isInteger(mb) || mb < MIN_MB || mb > MAX_MB) return null;
  return mb;
}

/** "10 MB" — the limit as the copy names it. */
export function formatMb(mb: number): string {
  return `${mb} MB`;
}

export const ACTIONS = ["drop", "partial", "allow"] as const satisfies readonly AppSecBodyAction[];

/** The approved copy for the three choices (design doc, "Decided in the brainstorm"). */
export const ACTION_COPY: Record<
  AppSecBodyAction,
  { title: string; tag: string; explain: (limit: string) => string; gain: string; cost: string }
> = {
  drop: {
    title: "Block it",
    tag: "drop · CrowdSec's default",
    explain: () => "The request is refused with the ban page, and its body is never checked.",
    gain: "Nothing gets past the WAF unchecked",
    cost: "Uploads bigger than the limit fail",
  },
  partial: {
    title: "Check the first part",
    tag: "partial",
    explain: (limit) =>
      `The first ${limit} is checked against the WAF rules, and the rest passes through unchecked.`,
    gain: "Big uploads work; most of the body is still checked",
    cost: "An attack hidden past the limit is missed",
  },
  allow: {
    title: "Let it through without checking the body",
    tag: "allow",
    explain: () =>
      "The body is skipped entirely. The address, URL, query and headers are still checked.",
    gain: "Big uploads work, at the lowest memory cost",
    cost: "Nothing in an oversized body is checked",
  },
};

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** The card's status line: what is live, and how the last apply went. */
export function describeWafRun(status: CrowdSecWafStatus): { label: string; failed: boolean } {
  const { run } = status;
  if (status.running || (run && !run.finished_at)) {
    return { label: "Applying… CrowdSec is restarting.", failed: false };
  }
  if (!run) {
    // Nothing has been applied, so only the seeded defaults can be live.
    if (status.max_body_mb !== DEFAULT_MB || status.body_exceeded_action !== DEFAULT_ACTION) {
      return { label: "Saved — not applied yet.", failed: false };
    }
    const title = ACTION_COPY[DEFAULT_ACTION].title;
    return { label: `Live: ${formatMb(DEFAULT_MB)} · ${title} — the default.`, failed: false };
  }
  if (!run.ok) return { label: `Failed: ${run.error ?? "unknown error"}`, failed: true };
  const appliedMb = Number(run.detail.max_body_bytes) / (1024 * 1024);
  if (appliedMb !== status.max_body_mb || run.detail.action !== status.body_exceeded_action) {
    return { label: "Saved — not applied yet.", failed: false };
  }
  const switchedOff =
    run.detail.blocklist_switched_off === true
      ? " The community blocklist was switched off: CrowdSec's servers were blocking this address."
      : "";
  const restarted = run.restarted ? " — CrowdSec restarted." : ".";
  return { label: `Applied ${when(run.started_at)}${restarted}${switchedOff}`, failed: false };
}
