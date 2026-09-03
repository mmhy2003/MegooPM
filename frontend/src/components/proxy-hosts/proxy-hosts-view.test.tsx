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

describe("ProxyHostsView search", () => {
  const ROWS = [
    makeHost({
      id: 1,
      domain_names: ["api.example.com"],
      upstream_id: null,
      forward_host: "10.0.0.5",
      forward_port: 8080,
    }),
    makeHost({
      id: 2,
      domain_names: ["blog.internal"],
      upstream_id: null,
      forward_host: "10.0.0.6",
      forward_port: 8080,
    }),
  ];

  async function renderView(rows = ROWS) {
    vi.spyOn(proxyHosts, "list").mockResolvedValue(rows);
    vi.spyOn(upstreams, "list").mockResolvedValue([]);
    vi.spyOn(accessLists, "list").mockResolvedValue([]);
    vi.spyOn(certificates, "list").mockResolvedValue([]);
    render(<ProxyHostsView />);
    await screen.findByRole("searchbox", { name: "Search proxy hosts" });
  }

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("narrows the table to matching hosts", async () => {
    const user = userEvent.setup();
    await renderView();

    await user.type(screen.getByRole("searchbox"), "blog");

    expect(screen.getByText("blog.internal")).toBeInTheDocument();
    expect(screen.queryByText("api.example.com")).not.toBeInTheDocument();
  });

  it("matches the forward target as well as the domain", async () => {
    const user = userEvent.setup();
    await renderView();

    await user.type(screen.getByRole("searchbox"), "10.0.0.6");

    expect(screen.getByText("blog.internal")).toBeInTheDocument();
    expect(screen.queryByText("api.example.com")).not.toBeInTheDocument();
  });

  it("restores every row when the search is cleared", async () => {
    const user = userEvent.setup();
    await renderView();
    await user.type(screen.getByRole("searchbox"), "blog");

    await user.click(screen.getByRole("button", { name: "Clear search proxy hosts" }));

    expect(screen.getByText("api.example.com")).toBeInTheDocument();
    expect(screen.getByText("blog.internal")).toBeInTheDocument();
  });

  it("says a filter is hiding the rows, not that there are none", async () => {
    // The bug this exists for: a filtered-empty table that reads like an empty
    // install sends an operator hunting for a bug that is a stale search box.
    const user = userEvent.setup();
    await renderView();

    await user.type(screen.getByRole("searchbox"), "nonesuch");

    expect(screen.getByText(/no proxy hosts match/i)).toBeInTheDocument();
    expect(screen.queryByText(/no proxy hosts yet/i)).not.toBeInTheDocument();
  });

  it("still says 'none yet' when the instance really is empty", async () => {
    await renderView([]);
    expect(screen.getByText(/no proxy hosts yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/no proxy hosts match/i)).not.toBeInTheDocument();
  });

  it("offers a way out of a filter that matches nothing", async () => {
    const user = userEvent.setup();
    await renderView();
    await user.type(screen.getByRole("searchbox"), "nonesuch");

    // Exact name: the box's own clear button is "Clear search proxy hosts", and
    // this test is about the escape hatch offered inside the empty table.
    await user.click(screen.getByRole("button", { name: "Clear search" }));

    expect(screen.getByText("api.example.com")).toBeInTheDocument();
  });
});
