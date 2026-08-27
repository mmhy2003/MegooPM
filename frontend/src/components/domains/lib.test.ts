import { describe, expect, it } from "vitest";

import { addDomains, isValidDomain, normalizeDomain, parseDomains } from "@/components/domains/lib";

describe("isValidDomain (mirrors the backend hostname rule)", () => {
  it.each([
    "example.com",
    "sub.example.co.uk",
    "*.example.com",
    "localhost",
    "a-b.example.org",
    "xn--bcher-kva.example",
  ])("accepts %s", (name) => {
    expect(isValidDomain(name)).toBe(true);
  });

  it.each([
    "",
    "exa mple.com",
    "-bad.example.com",
    "bad-.example.com",
    "example..com",
    "*.*.example.com",
    "foo.*.example.com",
    "http://example.com",
    "example.com/path",
  ])("rejects %s", (name) => {
    expect(isValidDomain(name)).toBe(false);
  });
});

describe("normalizeDomain", () => {
  it("trims and lower-cases", () => {
    expect(normalizeDomain("  Example.COM ")).toBe("example.com");
  });
});

describe("parseDomains", () => {
  it("splits on commas, whitespace and newlines, normalises and de-duplicates", () => {
    expect(parseDomains("A.com, b.com\nc.com  a.com")).toEqual(["a.com", "b.com", "c.com"]);
    expect(parseDomains("   ")).toEqual([]);
  });
});

describe("addDomains", () => {
  it("appends valid, new domains and reports the invalid ones", () => {
    expect(addDomains(["a.com"], "B.com, a.com, bad_name, *.c.com")).toEqual({
      next: ["a.com", "b.com", "*.c.com"],
      rejected: ["bad_name"],
    });
  });

  it("returns the existing list untouched for blank input", () => {
    expect(addDomains(["a.com"], "  ")).toEqual({ next: ["a.com"], rejected: [] });
  });
});
