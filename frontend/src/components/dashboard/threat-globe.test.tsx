import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { ThreatGlobe } from "@/components/dashboard/threat-globe";

// jsdom has no WebGL, so the globe cannot draw here. That is the point: the
// component must still present its data, which is exactly what a screen reader
// gets too.
vi.mock("cobe", () => ({
  default: () => {
    throw new Error("no webgl in jsdom");
  },
}));

afterEach(() => cleanup());

describe("ThreatGlobe", () => {
  it("distinguishes 'nothing was flagged' from 'nothing happened'", () => {
    render(<ThreatGlobe points={[]} />);
    expect(screen.getByText(/no attacks flagged/i)).toBeInTheDocument();
    expect(screen.getByText(/not that nothing happened/i)).toBeInTheDocument();
  });

  it("lists origins as text, so the data survives without a canvas", () => {
    render(<ThreatGlobe points={[{ country: "DE", count: 9, lat: 51, lng: 10 }]} />);
    expect(screen.getByText("DE")).toBeInTheDocument();
    expect(screen.getByText("9")).toBeInTheDocument();
  });

  it("still lists a country that could not be located", () => {
    // Dropping it would hide a real attacker to keep the map tidy.
    render(<ThreatGlobe points={[{ country: "ZZ", count: 4, lat: null, lng: null }]} />);
    expect(screen.getByText("ZZ")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText(/not located/i)).toBeInTheDocument();
  });

  it("says the map covers flagged requests rather than all traffic", () => {
    // An operator reading this as a traffic map would badly misjudge their load.
    render(<ThreatGlobe points={[{ country: "FR", count: 1, lat: 46, lng: 2 }]} />);
    expect(screen.getByText(/only what was flagged/i)).toBeInTheDocument();
  });

  it("orders origins as given, busiest first", () => {
    render(
      <ThreatGlobe
        points={[
          { country: "DE", count: 9, lat: 51, lng: 10 },
          { country: "FR", count: 2, lat: 46, lng: 2 },
        ]}
      />,
    );
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items[0]).toContain("DE");
    expect(items[1]).toContain("FR");
  });
});
