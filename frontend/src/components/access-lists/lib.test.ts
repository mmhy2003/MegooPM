import { describe, expect, it } from "vitest";

import { normalizeAddress, satisfyDescription, satisfyLabel } from "./lib";

describe("satisfyLabel", () => {
  it("maps satisfy_any to the gate word", () => {
    expect(satisfyLabel(true)).toBe("Any");
    expect(satisfyLabel(false)).toBe("All");
  });
});

describe("satisfyDescription", () => {
  it("describes an OR gate when satisfy_any", () => {
    expect(satisfyDescription(true)).toMatch(/EITHER/);
  });
  it("describes an AND gate otherwise", () => {
    expect(satisfyDescription(false)).toMatch(/BOTH/);
  });
});

describe("normalizeAddress", () => {
  it("trims surrounding whitespace", () => {
    expect(normalizeAddress("  10.0.0.1 ")).toBe("10.0.0.1");
  });
  it("lower-cases the 'all' keyword", () => {
    expect(normalizeAddress("ALL")).toBe("all");
    expect(normalizeAddress(" All ")).toBe("all");
  });
  it("preserves other addresses verbatim (backend validates)", () => {
    expect(normalizeAddress("192.168.0.0/16")).toBe("192.168.0.0/16");
    expect(normalizeAddress("2001:DB8::/32")).toBe("2001:DB8::/32");
  });
});
