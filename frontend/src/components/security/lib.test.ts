import { describe, expect, it } from "vitest";

import type { Alert } from "@/lib/api";
import {
  buildTimeline,
  clampPage,
  decisionRowKey,
  formatRelativeTime,
  pageCount,
  parseTimestamp,
  rangeLabel,
  topOffenders,
} from "@/components/security/lib";

const NOW = Date.parse("2026-08-27T12:00:00Z");
const HOUR = 60 * 60 * 1000;

function alertAt(iso: string | null, source?: string | null, events?: number): Alert {
  return {
    start_at: iso,
    source: source ? { value: source } : null,
    events_count: events ?? null,
  };
}

describe("parseTimestamp", () => {
  it("returns epoch ms for a valid ISO string", () => {
    expect(parseTimestamp("2026-08-27T12:00:00Z")).toBe(NOW);
  });

  it("returns null for absent or unparseable input", () => {
    expect(parseTimestamp(null)).toBeNull();
    expect(parseTimestamp(undefined)).toBeNull();
    expect(parseTimestamp("not-a-date")).toBeNull();
  });
});

describe("formatRelativeTime", () => {
  it("bins into just-now / minutes / hours / days", () => {
    expect(formatRelativeTime(new Date(NOW - 10_000).toISOString(), NOW)).toBe("just now");
    expect(formatRelativeTime(new Date(NOW - 5 * 60_000).toISOString(), NOW)).toBe("5m ago");
    expect(formatRelativeTime(new Date(NOW - 3 * HOUR).toISOString(), NOW)).toBe("3h ago");
    expect(formatRelativeTime(new Date(NOW - 2 * 24 * HOUR).toISOString(), NOW)).toBe("2d ago");
  });

  it("renders an em dash when the timestamp is missing", () => {
    expect(formatRelativeTime(null, NOW)).toBe("—");
  });
});

describe("buildTimeline", () => {
  const opts = { nowMs: NOW, bucketMs: HOUR, buckets: 4 };

  it("returns exactly `buckets` entries, oldest first", () => {
    const timeline = buildTimeline([], opts);
    expect(timeline).toHaveLength(4);
    expect(timeline[0].startMs).toBeLessThan(timeline[3].startMs);
    expect(timeline.every((b) => b.count === 0)).toBe(true);
  });

  it("counts alerts into the correct bucket", () => {
    const timeline = buildTimeline(
      [
        alertAt(new Date(NOW - 30 * 60_000).toISOString()), // newest bucket
        alertAt(new Date(NOW - 30 * 60_000).toISOString()), // newest bucket
        alertAt(new Date(NOW - 2.5 * HOUR).toISOString()), // 3rd-from-now bucket
      ],
      opts,
    );
    expect(timeline[3].count).toBe(2);
    expect(timeline[1].count).toBe(1);
  });

  it("drops alerts older than the window and clamps future-dated ones", () => {
    const timeline = buildTimeline(
      [
        alertAt(new Date(NOW - 10 * HOUR).toISOString()), // too old → dropped
        alertAt(new Date(NOW + HOUR).toISOString()), // future → newest bucket
      ],
      opts,
    );
    expect(timeline.reduce((s, b) => s + b.count, 0)).toBe(1);
    expect(timeline[3].count).toBe(1);
  });
});

describe("topOffenders", () => {
  it("aggregates events per source and ranks highest first", () => {
    const result = topOffenders([
      alertAt("2026-08-27T11:00:00Z", "1.1.1.1", 3),
      alertAt("2026-08-27T11:30:00Z", "1.1.1.1", 2),
      alertAt("2026-08-27T11:45:00Z", "2.2.2.2", 4),
    ]);
    expect(result).toEqual([
      { key: "1.1.1.1", count: 5 },
      { key: "2.2.2.2", count: 4 },
    ]);
  });

  it("treats a missing/zero event count as one and skips sourceless alerts", () => {
    const result = topOffenders([
      alertAt("2026-08-27T11:00:00Z", "3.3.3.3"),
      alertAt("2026-08-27T11:00:00Z", "3.3.3.3", 0),
      alertAt("2026-08-27T11:00:00Z", null),
    ]);
    expect(result).toEqual([{ key: "3.3.3.3", count: 2 }]);
  });

  it("caps the result at the requested limit", () => {
    const alerts = ["a", "b", "c", "d"].map((k, i) => alertAt("2026-08-27T11:00:00Z", k, i + 1));
    expect(topOffenders(alerts, 2)).toHaveLength(2);
  });
});

describe("decisionRowKey", () => {
  it("prefers the LAPI id and falls back to scope/value/index", () => {
    expect(decisionRowKey({ id: 7, scope: "Ip", value: "1.1.1.1", type: "ban", duration: "4h" }, 0)).toBe(
      "id-7",
    );
    expect(
      decisionRowKey({ id: null, scope: "Range", value: "10.0.0.0/24", type: "ban", duration: "4h" }, 2),
    ).toBe("Range:10.0.0.0/24:2");
  });
});

describe("pageCount", () => {
  it("rounds up partial pages and never drops below 1", () => {
    expect(pageCount(0, 50)).toBe(1);
    expect(pageCount(50, 50)).toBe(1);
    expect(pageCount(51, 50)).toBe(2);
    expect(pageCount(213, 50)).toBe(5);
  });

  it("degrades gracefully for a non-positive page size", () => {
    expect(pageCount(100, 0)).toBe(1);
  });
});

describe("clampPage", () => {
  it("keeps an in-range page and pulls an over-range page back to the last", () => {
    expect(clampPage(3, 213, 50)).toBe(3);
    expect(clampPage(99, 213, 50)).toBe(5); // past the end → last page
  });

  it("floors invalid pages to 1", () => {
    expect(clampPage(0, 213, 50)).toBe(1);
    expect(clampPage(-2, 213, 50)).toBe(1);
    expect(clampPage(Number.NaN, 213, 50)).toBe(1);
  });

  it("clamps to 1 when there are no records", () => {
    expect(clampPage(4, 0, 50)).toBe(1);
  });
});

describe("rangeLabel", () => {
  it("describes the visible slice and clamps the last partial page", () => {
    expect(rangeLabel(1, 50, 213)).toBe("1–50 of 213");
    expect(rangeLabel(5, 50, 213)).toBe("201–213 of 213");
  });

  it("reads as empty when there are no records", () => {
    expect(rangeLabel(1, 50, 0)).toBe("0 of 0");
  });
});
