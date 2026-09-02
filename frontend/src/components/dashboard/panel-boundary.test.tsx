import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { PanelBoundary } from "@/components/dashboard/panel-boundary";

function Boom(): never {
  throw new Error("panel exploded");
}

beforeEach(() => {
  // React logs caught errors to console.error; silence it so a passing run is
  // not full of red text that looks like a failure.
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("PanelBoundary", () => {
  it("contains a failure instead of unmounting the page", () => {
    // The bug this exists for: a throw inside one panel took down the whole
    // dashboard and left Next's error screen — on the page an operator opens
    // precisely when something is already wrong.
    render(
      <div>
        <PanelBoundary title="Request origins">
          <Boom />
        </PanelBoundary>
        <p>the rest of the dashboard</p>
      </div>,
    );

    expect(screen.getByText("the rest of the dashboard")).toBeInTheDocument();
    expect(screen.getByText(/failed to render/i)).toBeInTheDocument();
  });

  it("names the panel that failed", () => {
    render(
      <PanelBoundary title="Request origins">
        <Boom />
      </PanelBoundary>,
    );
    expect(screen.getByText("Request origins")).toBeInTheDocument();
  });

  it("leaves a working panel alone", () => {
    render(
      <PanelBoundary title="Request origins">
        <p>real content</p>
      </PanelBoundary>,
    );
    expect(screen.getByText("real content")).toBeInTheDocument();
    expect(screen.queryByText(/failed to render/i)).not.toBeInTheDocument();
  });

  it("still reports the error somewhere an operator can find it", () => {
    render(
      <PanelBoundary title="Request origins">
        <Boom />
      </PanelBoundary>,
    );
    // The panel says only that it failed; the console carries the why.
    expect(console.error).toHaveBeenCalled();
  });
});
