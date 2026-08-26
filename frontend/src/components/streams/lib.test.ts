import { describe, expect, it } from "vitest";

import { parsePort } from "@/components/streams/lib";

describe("parsePort", () => {
  it("accepts valid ports across the 1–65535 range", () => {
    expect(parsePort("1")).toBe(1);
    expect(parsePort("5432")).toBe(5432);
    expect(parsePort("65535")).toBe(65535);
  });

  it("trims surrounding whitespace", () => {
    expect(parsePort("  443 ")).toBe(443);
  });

  it("rejects out-of-range ports", () => {
    expect(parsePort("0")).toBeNull();
    expect(parsePort("65536")).toBeNull();
    expect(parsePort("-1")).toBeNull();
  });

  it("rejects non-numeric and mixed input", () => {
    expect(parsePort("")).toBeNull();
    expect(parsePort("abc")).toBeNull();
    expect(parsePort("80abc")).toBeNull();
    expect(parsePort("80.5")).toBeNull();
  });
});
