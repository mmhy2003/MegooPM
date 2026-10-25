import { describe, expect, it } from "vitest";

import {
  appendCapped,
  matches,
  statusClass,
  TAB_OF,
  toText,
  type LiveLine,
} from "@/components/live-logs/lib";

function line(over: Partial<LiveLine> = {}): LiveLine {
  return {
    id: 1,
    at: 0,
    node: "local",
    host: "p1",
    log: "request",
    sev: 6,
    f: { ts: "1700000000.123", st: "200", m: "GET", u: "/Apps/Files", ip: "5.6.7.8", ua: "Curl/8" },
    raw: null,
    truncated: false,
    cc: null,
    ...over,
  };
}

describe("TAB_OF", () => {
  it("files every log kind under its tab", () => {
    expect(TAB_OF).toEqual({
      request: "requests",
      error: "errors",
      security: "security",
      decision: "security",
      stream: "streams",
    });
  });
});

describe("appendCapped", () => {
  it("keeps the newest 1,000", () => {
    const old = Array.from({ length: 1000 }, (_, i) => line({ id: i }));
    const out = appendCapped(old, [line({ id: 1000 }), line({ id: 1001 })]);
    expect(out).toHaveLength(1000);
    expect(out[0]!.id).toBe(2);
    expect(out[999]!.id).toBe(1001);
  });

  it("honours a smaller cap and does not mutate its input", () => {
    const old = [line({ id: 1 }), line({ id: 2 })];
    const out = appendCapped(old, [line({ id: 3 })], 2);
    expect(out.map((l) => l.id)).toEqual([2, 3]);
    expect(old).toHaveLength(2);
  });
});

describe("statusClass", () => {
  it("names the class of a status", () => {
    expect(statusClass("200")).toBe("2xx");
    expect(statusClass(404)).toBe("4xx");
    expect(statusClass("502")).toBe("5xx");
    expect(statusClass("-")).toBeNull();
    expect(statusClass(undefined)).toBeNull();
  });
});

describe("matches", () => {
  const any = { node: "", status: "", text: "" };

  it("passes everything with no filter", () => {
    expect(matches(line(), any)).toBe(true);
  });

  it("filters by node", () => {
    expect(matches(line({ node: "a" }), { ...any, node: "a" })).toBe(true);
    expect(matches(line({ node: "a" }), { ...any, node: "b" })).toBe(false);
  });

  it("filters by status class", () => {
    expect(matches(line(), { ...any, status: "2xx" })).toBe(true);
    expect(matches(line(), { ...any, status: "5xx" })).toBe(false);
  });

  it("filters by text over path, IP and user agent, ignoring case", () => {
    expect(matches(line(), { ...any, text: "apps/files" })).toBe(true);
    expect(matches(line(), { ...any, text: "5.6.7" })).toBe(true);
    expect(matches(line(), { ...any, text: "CURL" })).toBe(true);
    expect(matches(line(), { ...any, text: "nope" })).toBe(false);
  });

  it("searches the raw text of an error line", () => {
    const err = line({ log: "error", f: null, raw: "connect() failed" });
    expect(matches(err, { ...any, text: "failed" })).toBe(true);
  });
});

describe("toText", () => {
  it("writes one line per entry", () => {
    const out = toText([line(), line({ log: "error", f: null, raw: "boom" })]);
    const rows = out.split("\n");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toContain("200 GET /Apps/Files");
    expect(rows[1]).toContain("boom");
  });
});
