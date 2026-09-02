import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { OriginGlobe } from "@/components/dashboard/origin-globe";

// jsdom has no WebGL, so the globe cannot draw here. That is the point: the
// component must still present its data, which is exactly what a screen reader
// gets, and it keeps these tests independent of the rendering technology.
vi.mock("cobe", () => ({
  default: () => {
    throw new Error("no webgl in jsdom");
  },
}));

const THREATS = [{ country: "DE", count: 9 }];
const TRAFFIC = [{ country: "FR", visitors: 3, requests: 40 }];

afterEach(() => cleanup());

describe("OriginGlobe", () => {
  it("shows traffic first, because it describes the whole site", () => {
    render(<OriginGlobe threats={THREATS} traffic={TRAFFIC} />);
    expect(screen.getByText("FR")).toBeInTheDocument();
    expect(screen.queryByText("DE")).not.toBeInTheDocument();
  });

  it("switches the list when the layer changes", async () => {
    const user = userEvent.setup();
    render(<OriginGlobe threats={THREATS} traffic={TRAFFIC} />);

    await user.click(screen.getByRole("button", { name: /threats/i }));

    expect(screen.getByText("DE")).toBeInTheDocument();
    // The list and the globe must never describe different datasets.
    expect(screen.queryByText("FR")).not.toBeInTheDocument();
  });

  it("marks the active layer for assistive technology", async () => {
    const user = userEvent.setup();
    render(<OriginGlobe threats={THREATS} traffic={TRAFFIC} />);

    expect(screen.getByRole("button", { name: /traffic/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await user.click(screen.getByRole("button", { name: /threats/i }));

    expect(screen.getByRole("button", { name: /threats/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: /traffic/i })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("says something different for each empty layer", async () => {
    const user = userEvent.setup();
    render(<OriginGlobe threats={[]} traffic={[]} />);

    expect(screen.getByText(/no visitors recorded/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /threats/i }));

    expect(screen.getByText(/no attacks flagged/i)).toBeInTheDocument();
    // An empty threat list must keep saying what it does NOT mean.
    expect(screen.getByText(/not that nothing happened/i)).toBeInTheDocument();
  });

  it("lists a country it cannot place, with its count", async () => {
    // Dropping it would understate the data to keep the map tidy.
    const user = userEvent.setup();
    render(<OriginGlobe threats={[{ country: "ZZ", count: 4 }]} traffic={[]} />);

    await user.click(screen.getByRole("button", { name: /threats/i }));

    expect(screen.getByText("ZZ")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText(/not located/i)).toBeInTheDocument();
  });

  it("keeps the order the API supplied", () => {
    // The backend already ranks by volume; reshuffling here would make the two
    // disagree for no reason.
    render(
      <OriginGlobe
        threats={[]}
        traffic={[
          { country: "DE", visitors: 1, requests: 5 },
          { country: "FR", visitors: 9, requests: 90 },
        ]}
      />,
    );
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items[0]).toContain("DE");
    expect(items[1]).toContain("FR");
  });

  it("says the threat layer covers only flagged requests", async () => {
    // An operator reading the threat map as a traffic map would badly misjudge
    // their load; the traffic layer is the one that describes all requests.
    const user = userEvent.setup();
    render(<OriginGlobe threats={THREATS} traffic={TRAFFIC} />);

    await user.click(screen.getByRole("button", { name: /threats/i }));

    expect(screen.getByText(/only what was flagged/i)).toBeInTheDocument();
  });
});
