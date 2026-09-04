import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

import { SecurityMetrics } from "@/components/security/security-metrics";

afterEach(cleanup);

const NOW = Date.parse("2026-09-05T12:00:00Z");
const HOUR = 60 * 60 * 1000;

function alertAt(ms: number) {
  return {
    id: ms,
    scenario: "x",
    message: null,
    events_count: 1,
    source: { ip: "1.1.1.1" },
    decisions: [],
    created_at: new Date(ms).toISOString(),
    start_at: new Date(ms).toISOString(),
    stop_at: null,
  };
}

describe("AlertsTimeline bars", () => {
  it("sizes each bar to its share of the busiest bucket, in a column that can hold it", () => {
    // Four alerts in the newest bucket, one in the bucket before it.
    const alerts = [
      alertAt(NOW - 10 * 60_000),
      alertAt(NOW - 20 * 60_000),
      alertAt(NOW - 30 * 60_000),
      alertAt(NOW - 40 * 60_000),
      alertAt(NOW - 3 * HOUR),
    ];
    render(<SecurityMetrics decisions={[]} alerts={alerts as never} nowMs={NOW} />);

    const chart = screen.getByRole("img", { name: /5 alerts over the last 24 hours/i });
    const columns = Array.from(chart.children) as HTMLElement[];
    expect(columns).toHaveLength(12);

    const heights = columns.map(
      (c) => (c.querySelector('[data-slot="bar"]') as HTMLElement).style.height,
    );
    expect(heights[11]).toBe("100%");
    expect(heights[10]).toBe("25%");
    expect(heights[0]).toBe("0%");

    // The count sits above each non-empty bar; empty buckets keep the label
    // row (so every bar scales against the same height) but show nothing.
    expect(within(columns[11]).getByText("4")).toBeInTheDocument();
    expect(within(columns[10]).getByText("1")).toBeInTheDocument();
    expect(columns[0].textContent).toBe("");

    // A percentage height only resolves against a column with a definite
    // height. With `items-end` on the row the column shrank to its content and
    // every bar collapsed to the 4px minimum — three flat lines on a live
    // dashboard. The column must stretch to the row's fixed height.
    for (const column of columns) {
      expect(column.className).toMatch(/\bh-full\b/);
    }
  });
});
