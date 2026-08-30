import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { upstreams, type Upstream } from "@/lib/api";
import { UpstreamsView } from "@/components/upstreams/upstreams-view";

function makePool(over: Partial<Upstream> = {}): Upstream {
  return {
    id: 1,
    name: "app-pool",
    description: "",
    lb_method: "round_robin",
    enabled: true,
    backends: [],
    created_at: "2026-08-27T00:00:00Z",
    updated_at: "2026-08-27T00:00:00Z",
    ...over,
  };
}

describe("UpstreamsView", () => {
  beforeEach(() => {
    vi.spyOn(toast, "error").mockImplementation(() => "" as never);
    vi.spyOn(upstreams, "list").mockResolvedValue([makePool()]);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("lists pools on their own page, with no tab to open first", async () => {
    render(<UpstreamsView />);
    expect(await screen.findByText("app-pool")).toBeInTheDocument();
    // Pools used to live behind an "Upstream pools" tab on the proxy hosts page.
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  it("toggles a pool", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(upstreams, "update").mockResolvedValue(makePool({ enabled: false }));
    render(<UpstreamsView />);

    await user.click(await screen.findByLabelText("Enable app-pool"));

    await waitFor(() => expect(update).toHaveBeenCalledWith(1, { enabled: false }));
  });

  it("reverts a toggle that fails", async () => {
    const user = userEvent.setup();
    vi.spyOn(upstreams, "update").mockRejectedValue(new Error("nope"));
    render(<UpstreamsView />);

    const toggle = await screen.findByLabelText("Enable app-pool");
    await user.click(toggle);

    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
    expect(toast.error).toHaveBeenCalled();
  });
});
