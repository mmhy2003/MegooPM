import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { OriginMap, bucketFor } from "@/components/dashboard/origin-map";

// jsdom cannot render the SVG map, so the library is stubbed out. That is the
// point: the component must still present its data, which is what assistive
// technology gets too, and it keeps these tests independent of the rendering
// library — they survived the move from cobe to jsvectormap unchanged.
vi.mock("jsvectormap", () => ({
  default: class {
    destroy() {}
  },
}));
vi.mock("jsvectormap/dist/maps/world", () => ({}));

const THREATS = [{ country: "DE", count: 9 }];
const TRAFFIC = [{ country: "FR", visitors: 3, requests: 40 }];

afterEach(() => cleanup());

describe("bucketFor", () => {
  it("puts the busiest country in the top band", () => {
    expect(bucketFor(100, 100)).toBe("b5");
  });

  it("puts a trivial share in the lowest band", () => {
    expect(bucketFor(1, 100)).toBe("b1");
  });

  it("spreads the middle rather than saturating it", () => {
    // Linear on the maximum: with one dominant country the rest must not all
    // land in the top band, or the map says everywhere is equally busy.
    expect(bucketFor(50, 100)).toBe("b3");
  });

  it("does not divide by zero when there is no traffic", () => {
    expect(bucketFor(0, 0)).toBe("b1");
  });
});

describe("OriginMap", () => {
  it("shows traffic first, because it describes the whole site", () => {
    render(<OriginMap threats={THREATS} traffic={TRAFFIC} />);
    expect(screen.getByRole("img", { name: "FR" })).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "DE" })).not.toBeInTheDocument();
  });

  it("switches the list when the layer changes", async () => {
    const user = userEvent.setup();
    render(<OriginMap threats={THREATS} traffic={TRAFFIC} />);

    await user.click(screen.getByRole("button", { name: /threats/i }));

    expect(screen.getByRole("img", { name: "DE" })).toBeInTheDocument();
    // The list and the map must never describe different datasets.
    expect(screen.queryByRole("img", { name: "FR" })).not.toBeInTheDocument();
  });

  it("marks the active layer for assistive technology", async () => {
    const user = userEvent.setup();
    render(<OriginMap threats={THREATS} traffic={TRAFFIC} />);

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
    render(<OriginMap threats={[]} traffic={[]} />);

    // Wording distinct from the Visitors card's: the same sentence in two
    // panels reads as a bug rather than two views of one absence.
    expect(
      screen.getByText(/no requests have been counted/i),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /threats/i }));

    expect(screen.getByText(/flagged no requests/i)).toBeInTheDocument();
    // An empty threat layer must keep saying what it does NOT mean.
    expect(screen.getByText(/not that nothing happened/i)).toBeInTheDocument();
  });

  it("lists every country with its count", async () => {
    // The choropleth shades what the world map knows; the list carries every
    // number regardless, so nothing is hidden to keep the map tidy.
    const user = userEvent.setup();
    render(<OriginMap threats={[{ country: "ZZ", count: 4 }]} traffic={[]} />);

    await user.click(screen.getByRole("button", { name: /threats/i }));

    expect(screen.getByRole("img", { name: "ZZ" })).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
  });

  it("keeps the order the API supplied", () => {
    // The backend already ranks by volume; reshuffling here would make the two
    // disagree for no reason.
    render(
      <OriginMap
        threats={[]}
        traffic={[
          { country: "DE", visitors: 1, requests: 5 },
          { country: "FR", visitors: 9, requests: 90 },
        ]}
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

  it("says the threat layer covers only flagged requests", async () => {
    // An operator reading the threat map as a traffic map would badly misjudge
    // their load.
    const user = userEvent.setup();
    render(<OriginMap threats={THREATS} traffic={TRAFFIC} />);

    await user.click(screen.getByRole("button", { name: /threats/i }));

    expect(screen.getByText(/only what was flagged/i)).toBeInTheDocument();
  });
});
