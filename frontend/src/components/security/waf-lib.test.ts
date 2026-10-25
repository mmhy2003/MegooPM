import { describe, expect, it } from "vitest";

import type { CrowdSecJobRun, CrowdSecWafStatus } from "@/lib/api";
import { ACTION_COPY, describeWafRun, toMb } from "@/components/security/waf-lib";

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
  it("takes whole megabytes up to 100", () => {
    expect(toMb("1")).toBe(1);
    expect(toMb(" 100 ")).toBe(100);
  });

  it("refuses what the API would refuse", () => {
    // 128 MiB and up is rejected by Coraza itself, whatever the action.
    expect(toMb("101")).toBeNull();
    expect(toMb("128")).toBeNull();
    expect(toMb("0")).toBeNull();
    expect(toMb("-1")).toBeNull();
    expect(toMb("")).toBeNull();
    expect(toMb("12.5")).toBeNull();
    expect(toMb("abc")).toBeNull();
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

  it("never calls a saved, unapplied value live or the default", () => {
    // First save on an install: the reload can land before the worker takes
    // the lock, so there is no run yet — but 100 MB / allow is not in force.
    expect(describeWafRun(status({ max_body_mb: 100, body_exceeded_action: "allow" })).label).toBe(
      "Saved — not applied yet.",
    );
    expect(describeWafRun(status({ body_exceeded_action: "drop" })).label).toBe(
      "Saved — not applied yet.",
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
