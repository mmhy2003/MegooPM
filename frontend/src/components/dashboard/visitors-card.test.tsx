import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { VisitorsCard } from "@/components/dashboard/visitors-card";
import type { VisitorSummary } from "@/lib/api";

function summary(overrides: Partial<VisitorSummary> = {}): VisitorSummary {
  return {
    days: 1,
    total_visitors: 0,
    total_requests: 0,
    countries: [],
    top_ips: [],
    ...overrides,
  };
}

afterEach(() => cleanup());

describe("VisitorsCard", () => {
  it("says nothing has been recorded rather than showing zeros", () => {
    // Before the first flush there is no measurement; "0 visitors" would read
    // as a quiet site rather than a pipeline that has not run yet.
    render(<VisitorsCard visitors={summary()} />);
    expect(screen.getByText(/no visitors recorded/i)).toBeInTheDocument();
  });

  it("shows the totals once traffic is recorded", () => {
    render(
      <VisitorsCard
        visitors={summary({ total_visitors: 9, total_requests: 400 })}
      />,
    );
    expect(screen.getByText("9")).toBeInTheDocument();
    expect(screen.getByText("400")).toBeInTheDocument();
  });

  it("lists countries by request volume", () => {
    render(
      <VisitorsCard
        visitors={summary({
          total_visitors: 9,
          total_requests: 400,
          countries: [
            { country: "DE", visitors: 5, requests: 300 },
            { country: "FR", visitors: 4, requests: 100 },
          ],
        })}
      />,
    );
    // By accessible name: the country is a flag now, so it is no longer part
    // of the row's text content.
    const flags = screen
      .getAllByRole("img")
      .map((el) => el.getAttribute("aria-label"));
    expect(flags[0]).toBe("DE");
    expect(flags[1]).toBe("FR");
  });

  it("shows an unlocated visitor rather than hiding it", () => {
    // The gap between the totals and the country list is real traffic.
    render(
      <VisitorsCard
        visitors={summary({
          total_visitors: 1,
          total_requests: 2,
          top_ips: [
            {
              ip: "1.2.3.4",
              country: null,
              requests: 2,
              last_seen_at: "2026-09-02T00:00:00Z",
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("1.2.3.4")).toBeInTheDocument();
    expect(screen.getByText(/unknown/i)).toBeInTheDocument();
  });

  it("says which window the numbers cover", () => {
    // "9 visitors" means nothing without knowing whether that is a day or a
    // month, and the window is clamped server-side so it may not be what was
    // asked for.
    render(<VisitorsCard visitors={summary({ days: 7, total_visitors: 9 })} />);
    expect(screen.getByText(/7 days/i)).toBeInTheDocument();
  });
});
