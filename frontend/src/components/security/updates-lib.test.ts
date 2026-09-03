import { describe, expect, it } from "vitest";

import {
  describeCapiRun,
  describeHubRun,
  localHourToUtc,
  utcHourToLocal,
} from "@/components/security/updates-lib";

// A fixed instant whose local offset the test computes itself, so it holds
// in any timezone the CI box runs in.
const NOW = new Date("2026-09-04T12:00:00Z");
const OFFSET_HOURS = -NOW.getTimezoneOffset() / 60;

describe("hour conversion", () => {
  it("round-trips through local time", () => {
    for (let h = 0; h < 24; h++) {
      expect(localHourToUtc(utcHourToLocal(h, NOW), NOW)).toBe(h);
    }
  });

  it("applies the browser's offset", () => {
    expect(utcHourToLocal(3, NOW)).toBe((((3 + OFFSET_HOURS) % 24) + 24) % 24);
  });
});

const run = (over: Record<string, unknown>) => ({
  kind: "hub_update" as const,
  started_at: "2026-09-04T03:05:00Z",
  finished_at: "2026-09-04T03:06:00Z",
  ok: true,
  error: null,
  trigger: "scheduled" as const,
  restarted: false,
  detail: {},
  ...over,
});

describe("describeHubRun", () => {
  it("has never run", () => {
    expect(describeHubRun(null)).toMatch(/never run/i);
  });
  it("no changes", () => {
    expect(describeHubRun(run({ detail: { updated: [] } }))).toMatch(/no changes/i);
  });
  it("counts updates and mentions the restart", () => {
    const text = describeHubRun(run({ restarted: true, detail: { updated: ["a", "b"] } }));
    expect(text).toMatch(/2 items updated/i);
    expect(text).toMatch(/restarted/i);
  });
  it("shows the error", () => {
    expect(describeHubRun(run({ ok: false, error: "hub upgrade failed: x" }))).toMatch(
      /hub upgrade failed/,
    );
  });
  it("says it is running", () => {
    expect(describeHubRun(run({ finished_at: null }))).toMatch(/running/i);
  });
});

describe("describeCapiRun", () => {
  it("off with nothing applied", () => {
    expect(describeCapiRun(false, null, false)).toEqual({ label: "Off", failed: false });
  });
  it("turning on while running", () => {
    expect(describeCapiRun(true, null, true).label).toMatch(/turning on/i);
  });
  it("on once applied", () => {
    expect(
      describeCapiRun(true, run({ kind: "capi_apply", detail: { enabled: true } }), false),
    ).toEqual({ label: "On", failed: false });
  });
  it("failed keeps the error", () => {
    const r = describeCapiRun(
      true,
      run({ kind: "capi_apply", ok: false, error: "no route", detail: { enabled: false } }),
      false,
    );
    expect(r.failed).toBe(true);
    expect(r.label).toMatch(/no route/);
  });
});
