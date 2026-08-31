import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { accessLists, certificates, proxyHosts, upstreams } from "@/lib/api";
import { ProxyHostsView } from "@/components/proxy-hosts/proxy-hosts-view";
import { makeHost } from "@/components/proxy-hosts/test-utils";

function mount() {
  vi.spyOn(proxyHosts, "list").mockResolvedValue([makeHost()]);
  // The hosts table still resolves pool names for its Upstream column, even
  // though pool management itself now lives on /upstreams.
  vi.spyOn(upstreams, "list").mockResolvedValue([]);
  vi.spyOn(accessLists, "list").mockResolvedValue([]);
  vi.spyOn(certificates, "list").mockResolvedValue([]);
  render(<ProxyHostsView />);
}

describe("ProxyHostsView enable toggle", () => {
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


});

describe("ProxyHostsView forward target", () => {
  beforeEach(() => {
    vi.spyOn(toast, "error").mockImplementation(() => "" as never);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows a literal backend for a host-targeted row", async () => {
    vi.spyOn(proxyHosts, "list").mockResolvedValue([
      makeHost({ upstream_id: null, forward_host: "10.0.0.1", forward_port: 8080 }),
    ]);
    vi.spyOn(upstreams, "list").mockResolvedValue([]);
    vi.spyOn(accessLists, "list").mockResolvedValue([]);
    vi.spyOn(certificates, "list").mockResolvedValue([]);
    render(<ProxyHostsView />);

    expect(await screen.findByText("10.0.0.1:8080")).toBeInTheDocument();
  });
});
