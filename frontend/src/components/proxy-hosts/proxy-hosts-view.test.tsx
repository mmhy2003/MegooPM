import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { accessLists, certificates, customPages, proxyHosts, upstreams } from "@/lib/api";
import { ProxyHostsView } from "@/components/proxy-hosts/proxy-hosts-view";
import { makeHost } from "@/components/proxy-hosts/test-utils";

// vi.mock factories are hoisted above the imports, so a factory closing
// over a plain const fails at collection. vi.hoisted is the way in.
const useAuth = vi.hoisted(() => vi.fn(() => ({ user: { role: "admin" } })));
vi.mock("@/lib/auth/context", () => ({ useAuth }));

const useIsMobile = vi.hoisted(() => vi.fn(() => false));
vi.mock("@/hooks/use-mobile", () => ({ useIsMobile }));

function mount() {
  vi.spyOn(proxyHosts, "list").mockResolvedValue([makeHost()]);
  // The hosts table still resolves pool names for its Upstream column, even
  // though pool management itself now lives on /upstreams.
  vi.spyOn(upstreams, "list").mockResolvedValue([]);
  vi.spyOn(accessLists, "list").mockResolvedValue([]);
  vi.spyOn(certificates, "list").mockResolvedValue([]);
  // The dialog offers stored pages as a location target.
  vi.spyOn(customPages, "list").mockResolvedValue([]);
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
    vi.spyOn(customPages, "list").mockResolvedValue([]);
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
    vi.spyOn(customPages, "list").mockResolvedValue([]);
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

describe("ProxyHostsView maintenance", () => {
  beforeEach(() => {
    vi.spyOn(toast, "error").mockImplementation(() => "" as never);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  function mountWith(host = makeHost()) {
    vi.spyOn(proxyHosts, "list").mockResolvedValue([host]);
    vi.spyOn(upstreams, "list").mockResolvedValue([]);
    vi.spyOn(accessLists, "list").mockResolvedValue([]);
    vi.spyOn(certificates, "list").mockResolvedValue([]);
    vi.spyOn(customPages, "list").mockResolvedValue([]);
    render(<ProxyHostsView />);
  }

  it("switches a host into maintenance from its row", async () => {
    // Planned downtime starts and ends at a moment someone is watching a
    // deploy; making them open a dialog to flip it adds a step to both ends.
    const user = userEvent.setup();
    const update = vi
      .spyOn(proxyHosts, "update")
      .mockResolvedValue(makeHost({ maintenance_enabled: true }));
    mountWith();

    await user.click(await screen.findByLabelText("Maintenance for app.example.com"));

    await waitFor(() => expect(update).toHaveBeenCalledWith(1, { maintenance_enabled: true }));
  });

  it("shows the switch on for a host already under maintenance", async () => {
    mountWith(makeHost({ maintenance_enabled: true }));

    expect(await screen.findByLabelText("Maintenance for app.example.com")).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("puts the switch back when the write fails", async () => {
    // Otherwise the row claims a host is under maintenance when it is still
    // serving traffic — the one state an operator must not be wrong about.
    const user = userEvent.setup();
    vi.spyOn(proxyHosts, "update").mockRejectedValue(new Error("nope"));
    mountWith();

    await user.click(await screen.findByLabelText("Maintenance for app.example.com"));

    await waitFor(() =>
      expect(screen.getByLabelText("Maintenance for app.example.com")).toHaveAttribute(
        "aria-checked",
        "false",
      ),
    );
    expect(toast.error).toHaveBeenCalled();
  });

  it("badges a host that is under maintenance", async () => {
    // The switch says which host; the badge is what makes a host left in
    // maintenance stand out while scanning a long list.
    mountWith(makeHost({ maintenance_enabled: true }));

    // Scoped to the badge itself: the column header carries the same word.
    expect(await screen.findByText("Maintenance", { selector: "span" })).toBeInTheDocument();
  });

  it("badges nothing when the host is serving normally", async () => {
    mount();
    await screen.findByRole("searchbox", { name: "Search proxy hosts" });

    expect(screen.queryByText("Maintenance", { selector: "span" })).not.toBeInTheDocument();
  });

  it("offers a member the state but not the control", async () => {
    useAuth.mockReturnValue({ user: { role: "member" } });
    mountWith(makeHost({ maintenance_enabled: true }));

    // base-ui renders the switch as a span, so `disabled` is aria-disabled.
    expect(await screen.findByLabelText("Maintenance for app.example.com")).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    useAuth.mockReturnValue({ user: { role: "admin" } });
  });
});

describe("ProxyHostsView write controls", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    useAuth.mockReturnValue({ user: { role: "admin" } });
  });

  it("offers no write controls to a member", async () => {
    // Hidden, not disabled: a disabled button invites a click and explains
    // nothing, and the API would refuse it anyway.
    useAuth.mockReturnValue({ user: { role: "member" } });
    mount();
    await screen.findByRole("searchbox", { name: "Search proxy hosts" });

    expect(screen.queryByRole("button", { name: /New proxy host/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Edit / })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Delete / })).not.toBeInTheDocument();
    expect(screen.getByText(/read-only/i)).toBeInTheDocument();
  });

  it("offers them to an admin", async () => {
    mount();
    expect(await screen.findByRole("button", { name: /New proxy host/i })).toBeInTheDocument();
    expect(screen.queryByText(/read-only/i)).not.toBeInTheDocument();
  });
});

describe("ProxyHostsView on a phone", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    useIsMobile.mockReturnValue(false);
  });

  it("lays each host out as a card, with both switches and its actions", async () => {
    useIsMobile.mockReturnValue(true);
    mount();

    expect(await screen.findByText("app.example.com")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Enable app.example.com")).toBeInTheDocument();
    expect(screen.getByLabelText("Maintenance for app.example.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit app.example.com" })).toBeInTheDocument();
  });
});
