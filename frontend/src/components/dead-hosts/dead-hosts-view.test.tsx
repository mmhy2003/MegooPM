import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { certificates, deadHosts, type DeadHost } from "@/lib/api";
import { DeadHostsView } from "@/components/dead-hosts/dead-hosts-view";

function makeDeadHost(over: Partial<DeadHost> = {}): DeadHost {
  return {
    id: 1,
    domain_names: ["parked.example.com"],
    certificate_id: null,
    enabled: true,
    ssl_forced: false,
    http2_support: false,
    hsts_enabled: false,
    hsts_subdomains: false,
    advanced_config: "",
    created_at: "2026-08-30T00:00:00Z",
    updated_at: "2026-08-30T00:00:00Z",
    ...over,
  };
}

async function renderView(rows: DeadHost[] = [makeDeadHost()]) {
  vi.spyOn(deadHosts, "list").mockResolvedValue(rows);
  vi.spyOn(certificates, "list").mockResolvedValue([]);
  render(<DeadHostsView />);
  return screen.findByLabelText("Enable parked.example.com");
}

describe("DeadHostsView enable toggle", () => {
  beforeEach(() => {
    vi.spyOn(toast, "error").mockImplementation(() => "" as never);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("patches only the enabled flag", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(deadHosts, "update").mockResolvedValue(makeDeadHost({ enabled: false }));
    const toggle = await renderView();

    await user.click(toggle);

    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update).toHaveBeenCalledWith(1, { enabled: false });
  });

  it("flips the row immediately, without reloading the table", async () => {
    const user = userEvent.setup();
    const list = vi.spyOn(deadHosts, "list").mockResolvedValue([makeDeadHost()]);
    vi.spyOn(deadHosts, "update").mockResolvedValue(makeDeadHost({ enabled: false }));
    const toggle = await renderView();
    const listCallsBefore = list.mock.calls.length;

    await user.click(toggle);

    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "false"));
    // A full refresh would set loading and flash skeleton rows over the table.
    expect(list.mock.calls.length).toBe(listCallsBefore);
  });

  it("reverts and reports when the request fails", async () => {
    const user = userEvent.setup();
    vi.spyOn(deadHosts, "update").mockRejectedValue(new Error("nope"));
    const toggle = await renderView();

    await user.click(toggle);

    // The row must go back to what the server still believes.
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
    expect(toast.error).toHaveBeenCalled();
  });
});
