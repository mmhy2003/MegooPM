import { describe, expect, it } from "vitest";

import type { CrowdSecJobRun, CrowdSecWafStatus } from "@/lib/api";
import { ACTION_COPY, describeWafRun, fromMb, toMb } from "@/components/security/waf-lib";

function status(over: Partial<CrowdSecWafStatus> = {}): CrowdSecWafStatus {
  return {
    max_body_mb: 10,
    body_exceeded_action: "partial",
    run: null,
    running: false,
    reload_configured: true,
    appsec_configured: true,
    protected_hosts: [],
    capi_enabled: false,
    capi_blocked: false,
    ...over,
  };
}

function run(over: Partial<CrowdSecJobRun> = {}): CrowdSecJobRun {
  return {
    kind: "waf_apply",
    ok: true,
    error: null,
    restarted: true,
    trigger: "manual",
    started_at: "2026-10-25T10:00:00Z",
    finished_at: "2026-10-25T10:00:20Z",
    detail: { max_body_bytes: 10485760, action: "partial", blocklist_switched_off: false },
    ...over,
  } as CrowdSecJobRun;
}

describe("toMb", () => {
  it("takes whole megabytes and gigabytes", () => {
    expect(toMb("100", "MB")).toBe(100);
    expect(toMb("1.5", "GB")).toBe(1536);
    expect(toMb("2", "GB")).toBe(2048);
  });

  it("refuses what the API would refuse", () => {
    expect(toMb("2049", "MB")).toBeNull();
    expect(toMb("3", "GB")).toBeNull();
    expect(toMb("0", "MB")).toBeNull();
    expect(toMb("-1", "MB")).toBeNull();
    expect(toMb("", "MB")).toBeNull();
    expect(toMb("12.5", "MB")).toBeNull();
    expect(toMb("abc", "MB")).toBeNull();
  });
});

describe("fromMb", () => {
  it("shows a whole number of gigabytes in GB", () => {
    expect(fromMb(2048)).toEqual({ value: "2", unit: "GB" });
    expect(fromMb(1024)).toEqual({ value: "1", unit: "GB" });
  });
  it("shows anything else in MB", () => {
    expect(fromMb(100)).toEqual({ value: "100", unit: "MB" });
    expect(fromMb(1536)).toEqual({ value: "1536", unit: "MB" });
  });
});

describe("ACTION_COPY", () => {
  it("carries the approved titles", () => {
    expect(ACTION_COPY.drop.title).toBe("Block it");
    expect(ACTION_COPY.partial.title).toBe("Check the first part");
    expect(ACTION_COPY.allow.title).toBe("Let it through without checking the body");
    expect(ACTION_COPY.partial.explain("100 MB")).toBe(
      "The first 100 MB is checked against the WAF rules, and the rest passes through unchecked.",
    );
  });
});

describe("describeWafRun", () => {
  it("reads the defaults as live before any apply", () => {
    expect(describeWafRun(status()).label).toBe(
      "Live: 10 MB · Check the first part — the default.",
    );
  });

  it("says it is applying", () => {
    expect(describeWafRun(status({ running: true })).label).toMatch(/applying/i);
  });

  it("keeps the error of a failed apply", () => {
    const s = status({ run: run({ ok: false, error: "CrowdSec did not come back" }) });
    expect(describeWafRun(s)).toEqual({
      label: "Failed: CrowdSec did not come back",
      failed: true,
    });
  });

  it("says when the saved value is not the applied one", () => {
    const s = status({ max_body_mb: 100, run: run() });
    expect(describeWafRun(s).label).toBe("Saved — not applied yet.");
  });

  it("mentions the blocklist when the safety net switched it off", () => {
    const s = status({
      run: run({
        detail: { max_body_bytes: 10485760, action: "partial", blocklist_switched_off: true },
      }),
    });
    expect(describeWafRun(s).label).toMatch(
      /The community blocklist was switched off: CrowdSec's servers were blocking this address\.$/,
    );
  });
});
