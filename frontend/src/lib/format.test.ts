import { describe, expect, it } from "vitest";

import { formatCount, formatExact } from "@/lib/format";

describe("formatCount", () => {
  it("leaves numbers under a thousand alone", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatCount(50)).toBe("50");
    expect(formatCount(999)).toBe("999");
  });

  it("shortens from a thousand with one decimal", () => {
    expect(formatCount(1000)).toBe("1K");
    expect(formatCount(5434)).toBe("5.4K");
    expect(formatCount(24270)).toBe("24.3K");
    expect(formatCount(1266434)).toBe("1.3M");
    expect(formatCount(2_500_000_000)).toBe("2.5B");
  });

  it("drops a trailing .0", () => {
    expect(formatCount(9973)).toBe("10K");
  });

  it("rolls over to the next suffix rather than reading 1000K", () => {
    expect(formatCount(999_950)).toBe("1M");
  });
});

describe("formatExact", () => {
  it("groups thousands", () => {
    expect(formatExact(1266434)).toBe("1,266,434");
  });
});
