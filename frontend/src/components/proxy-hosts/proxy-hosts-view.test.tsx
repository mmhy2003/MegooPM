import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import {
  accessLists,
  certificates,
  proxyHosts,
  upstreams,
  type Upstream,
} from "@/lib/api";
import { ProxyHostsView } from "@/components/proxy-hosts/proxy-hosts-view";
import { makeHost } from "@/components/proxy-hosts/test-utils";

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

/** The pools table lives behind the "Upstream pools" tab on this page. */
async function openPools(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("tab", { name: /Upstream pools/ }));
  return screen.findByLabelText("Enable app-pool");
}

function mount() {
  vi.spyOn(proxyHosts, "list").mockResolvedValue([makeHost()]);
  vi.spyOn(upstreams, "list").mockResolvedValue([makePool()]);
  vi.spyOn(accessLists, "list").mockResolvedValue([]);
  vi.spyOn(certificates, "list").mockResolvedValue([]);
  render(<ProxyHostsView />);
}

describe("ProxyHostsView enable toggles", () => {
  beforeEach(() => {
    vi.spyOn(toast, "error").mockImplementation(() => "" as never);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("toggles a proxy host", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(proxyHosts, "update").mockResolvedValue(makeHost({ enabled: false }));
    mount();

    await user.click(await screen.findByLabelText("Enable app.example.com"));

    await waitFor(() => expect(update).toHaveBeenCalledWith(1, { enabled: false }));
  });

  it("toggles an upstream pool", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(upstreams, "update").mockResolvedValue(makePool({ enabled: false }));
    mount();

    // Pools are a second table on this same page and carry the same flag, so
    // they get the same control — a badge there beside a switch on the Hosts
    // tab reads as a bug, and this is the table missed on the first pass.
    await user.click(await openPools(user));

    await waitFor(() => expect(update).toHaveBeenCalledWith(1, { enabled: false }));
  });

  it("reverts a pool toggle that fails", async () => {
    const user = userEvent.setup();
    vi.spyOn(upstreams, "update").mockRejectedValue(new Error("nope"));
    mount();

    const toggle = await openPools(user);
    await user.click(toggle);

    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
    expect(toast.error).toHaveBeenCalled();
  });
});
