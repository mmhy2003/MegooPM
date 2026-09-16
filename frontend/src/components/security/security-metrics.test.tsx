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

describe("large counts", () => {
  it("shortens the stat tiles, keeping the exact figure on hover", () => {
    render(
      <SecurityMetrics
        decisions={[]}
        alerts={[]}
        decisionsTotal={24270}
        alertsTotal={1266434}
        nowMs={NOW}
      />,
    );

    expect(screen.getByText("24.3K")).toBeInTheDocument();
    expect(screen.getByTitle("24,270")).toBeInTheDocument();
    expect(screen.getByText("1.3M")).toBeInTheDocument();
  });

  it("shortens an offender's event count, which shares a narrow column", () => {
    const noisy = { ...alertAt(NOW - 10 * 60_000), events_count: 169352 };
    render(<SecurityMetrics decisions={[]} alerts={[noisy] as never} nowMs={NOW} />);

    expect(screen.getByText("169.4K")).toBeInTheDocument();
    expect(screen.getByTitle("169,352")).toBeInTheDocument();
  });

  it("shortens the count above a busy bar", () => {
    // A bucket's label is 11px wide-ish; five digits there overlap its
    // neighbours.
    const alerts = Array.from({ length: 1200 }, () => alertAt(NOW - 10 * 60_000));
    render(<SecurityMetrics decisions={[]} alerts={alerts as never} nowMs={NOW} />);

    const chart = screen.getByRole("img", { name: /alerts over the last 24 hours/i });
    const columns = Array.from(chart.children) as HTMLElement[];
    expect(within(columns[11]).getByText("1.2K")).toBeInTheDocument();
  });
});

describe("the bans tile", () => {
  it("counts every ban matching the filter, not the ones on this page", () => {
    // With community decisions included the page is all bans, so a per-page
    // count reads 50 — the page size — however many bans there really are.
    const page = Array.from({ length: 50 }, (_, i) => ({
      type: "ban",
      scope: "Ip",
      value: `1.1.1.${i}`,
      duration: "1h",
    }));
    render(
      <SecurityMetrics
        decisions={page as never}
        alerts={[]}
        decisionsTotal={24270}
        bansTotal={23918}
        nowMs={NOW}
      />,
    );

    expect(screen.getByText("23.9K")).toBeInTheDocument();
    expect(screen.getByTitle("23,918")).toBeInTheDocument();
    expect(screen.getByText(/active bans/i)).toBeInTheDocument();
  });

  it("falls back to the page while the server total is missing", () => {
    const page = [
      { type: "ban", scope: "Ip", value: "1.1.1.1", duration: "1h" },
      { type: "captcha", scope: "Ip", value: "2.2.2.2", duration: "1h" },
      { type: "captcha", scope: "Ip", value: "3.3.3.3", duration: "1h" },
    ];
    render(<SecurityMetrics decisions={page as never} alerts={[]} nowMs={NOW} />);

    const tile = screen.getByText(/active bans/i).closest("div") as HTMLElement;
    expect(within(tile).getByText("1")).toBeInTheDocument();
  });
});
