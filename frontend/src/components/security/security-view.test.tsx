import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { crowdsec } from "@/lib/api";
import { SecurityView } from "@/components/security/security-view";

// Stub the ban/unban dialogs: they own their own Select/portal machinery which
// is irrelevant here — we only care that SecurityView wires their callbacks and
// re-fetches (ban/unban reflected without a full reload).
vi.mock("@/components/security/ban-dialog", () => ({
  BanDialog: ({ onSaved }: { onSaved: () => void }) => (
    <button type="button" onClick={onSaved}>
      confirm-ban
    </button>
  ),
}));
vi.mock("@/components/security/unban-dialog", () => ({
  UnbanDialog: ({ decision, onLifted }: { decision: { value: string }; onLifted: () => void }) => (
    <button type="button" onClick={onLifted}>
      confirm-unban {decision.value}
    </button>
  ),
}));

const healthOk = {
  configured: true,
  reachable: true,
  machine_registered: true,
  lapi_url: "http://lapi:8080",
  detail: null,
};

function decisionList(total: number) {
  return {
    total,
    page: 1,
    page_size: 50,
    items: [
      { id: 11, type: "ban", scope: "Ip", value: "203.0.113.9", duration: "4h", origin: "cscli" },
    ],
  };
}

const emptyAlerts = { total: 0, page: 1, page_size: 50, items: [] };

describe("SecurityView", () => {
  beforeEach(() => {
    vi.spyOn(crowdsec, "health").mockResolvedValue(healthOk as never);
    vi.spyOn(crowdsec, "listDecisions").mockResolvedValue(decisionList(120) as never);
    vi.spyOn(crowdsec, "listAlerts").mockResolvedValue(emptyAlerts as never);
    vi.spyOn(crowdsec, "listWhitelists").mockResolvedValue([] as never);
    vi.spyOn(crowdsec, "whitelistStatus").mockResolvedValue({
      ok: true,
      error: null,
      applied_at: null,
      reload_configured: true,
    } as never);
    vi.spyOn(crowdsec, "deleteDecision").mockResolvedValue({ deleted: 1 } as never);
    try {
      window.sessionStorage.clear();
    } catch {
      /* ignore */
    }
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("fetches the first page with community excluded by default", async () => {
    render(<SecurityView />);

    await waitFor(() =>
      expect(crowdsec.listDecisions).toHaveBeenCalledWith({
        page: 1,
        pageSize: 50,
        includeCommunity: false,
          q: "",
      }),
    );
    expect(crowdsec.listAlerts).toHaveBeenCalledWith({
      page: 1,
      pageSize: 50,
      includeCommunity: false,
      q: "",
    });
  });

  it("advances the decisions page when Next is clicked", async () => {
    const user = userEvent.setup();
    render(<SecurityView />);

    // Dashboard is the default tab now, so open the decisions list first.
    await user.click(await screen.findByRole("tab", { name: /Active decisions/ }));
    const panel = await screen.findByRole("tabpanel");
    await waitFor(() => expect(crowdsec.listDecisions).toHaveBeenCalled());

    await user.click(within(panel).getByRole("button", { name: "Next page" }));

    await waitFor(() =>
      expect(crowdsec.listDecisions).toHaveBeenLastCalledWith({
        page: 2,
        pageSize: 50,
        includeCommunity: false,
          q: "",
      }),
    );
  });

  it("includes community records and resets to page 1 when toggled on", async () => {
    const user = userEvent.setup();
    render(<SecurityView />);
    await waitFor(() => expect(crowdsec.listDecisions).toHaveBeenCalled());

    await user.click(screen.getByRole("switch", { name: /community/i }));

    await waitFor(() =>
      expect(crowdsec.listDecisions).toHaveBeenLastCalledWith({
        page: 1,
        pageSize: 50,
        includeCommunity: true,
          q: "",
      }),
    );
    expect(crowdsec.listAlerts).toHaveBeenLastCalledWith({
      page: 1,
      pageSize: 50,
      includeCommunity: true,
      q: "",
    });
  });

  it("re-fetches after an unban is confirmed", async () => {
    const user = userEvent.setup();
    render(<SecurityView />);
    // Dashboard is the default tab now, so open the decisions list first.
    await user.click(await screen.findByRole("tab", { name: /Active decisions/ }));
    const panel = await screen.findByRole("tabpanel");
    await waitFor(() => expect(crowdsec.listDecisions).toHaveBeenCalled());
    const callsBefore = vi.mocked(crowdsec.listDecisions).mock.calls.length;

    // Open the unban dialog from the row action, then confirm via the stub.
    await user.click(within(panel).getByRole("button", { name: /Lift decision on 203\.0\.113\.9/ }));
    await user.click(await screen.findByText(/confirm-unban 203\.0\.113\.9/));

    await waitFor(() =>
      expect(vi.mocked(crowdsec.listDecisions).mock.calls.length).toBeGreaterThan(callsBefore),
    );
  });
});

describe("SecurityView dashboard tab", () => {
  beforeEach(() => {
    vi.spyOn(crowdsec, "health").mockResolvedValue(healthOk as never);
    vi.spyOn(crowdsec, "listDecisions").mockResolvedValue(decisionList(120) as never);
    vi.spyOn(crowdsec, "listAlerts").mockResolvedValue(emptyAlerts as never);
    vi.spyOn(crowdsec, "listWhitelists").mockResolvedValue([] as never);
    vi.spyOn(crowdsec, "whitelistStatus").mockResolvedValue({
      ok: true,
      error: null,
      applied_at: null,
      reload_configured: true,
    } as never);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("puts Dashboard first and selects it by default", async () => {
    render(<SecurityView />);

    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((t) => t.textContent?.trim())).toEqual([
      "Dashboard",
      "Active decisions",
      "Recent alerts",
      "Whitelists",
    ]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
  });

  it("renders the whitelist table under its own tab", async () => {
    const user = userEvent.setup();
    render(<SecurityView />);

    await user.click(await screen.findByRole("tab", { name: /Whitelists/ }));

    // The empty state, not a blank panel: an operator who has never added a
    // whitelist should be told what the tab is for.
    expect(await screen.findByText(/No whitelists yet/i)).toBeInTheDocument();
  });

  it("hides the whitelist banner while applies are healthy", async () => {
    const user = userEvent.setup();
    render(<SecurityView />);

    await user.click(await screen.findByRole("tab", { name: /Whitelists/ }));

    expect(screen.queryByText(/CROWDSEC_CONTROL_NODE_ID/)).not.toBeInTheDocument();
  });

  it("shows the metrics under Dashboard, not under the list tabs", async () => {
    const user = userEvent.setup();
    render(<SecurityView />);

    expect(await screen.findByText("Alerts over time")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /Active decisions/ }));
    expect(screen.queryByText("Alerts over time")).not.toBeInTheDocument();
  });

  it("keeps the LAPI status visible from every tab", async () => {
    const user = userEvent.setup();
    vi.spyOn(crowdsec, "health").mockResolvedValue({
      ...healthOk,
      reachable: false,
      detail: "boom",
    } as never);
    render(<SecurityView />);

    // The banner explains why the lists below are failing, so it must not hide
    // behind a tab the operator is not on.
    expect(await screen.findByText("LAPI unreachable")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /Recent alerts/ }));
    expect(screen.getByText("LAPI unreachable")).toBeInTheDocument();
  });
});

describe("SecurityView whitelist search", () => {
  beforeEach(() => {
    vi.spyOn(crowdsec, "health").mockResolvedValue(healthOk as never);
    vi.spyOn(crowdsec, "listDecisions").mockResolvedValue(decisionList(120) as never);
    vi.spyOn(crowdsec, "listAlerts").mockResolvedValue(emptyAlerts as never);
    vi.spyOn(crowdsec, "whitelistStatus").mockResolvedValue({
      ok: true,
      error: null,
      applied_at: null,
      reload_configured: true,
    } as never);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("narrows the whitelists table", async () => {
    const user = userEvent.setup();
    vi.spyOn(crowdsec, "listWhitelists").mockResolvedValue([
      {
        id: 1,
        name: "office",
        kind: "ip_cidr",
        reason: "our egress",
        description: "",
        ips: ["203.0.113.4"],
        cidrs: [],
        filter: null,
        expressions: [],
        enabled: true,
        created_at: "2026-09-01T00:00:00Z",
        updated_at: "2026-09-01T00:00:00Z",
      },
      {
        id: 2,
        name: "monitoring",
        kind: "expression",
        reason: "uptime checks",
        description: "",
        ips: [],
        cidrs: [],
        filter: null,
        expressions: ["evt.Parsed.http_user_agent contains 'uptime'"],
        enabled: true,
        created_at: "2026-09-01T00:00:00Z",
        updated_at: "2026-09-01T00:00:00Z",
      },
    ] as never);
    render(<SecurityView />);
    await user.click(await screen.findByRole("tab", { name: /whitelists/i }));
    const box = await screen.findByRole("searchbox", { name: "Search whitelists" });

    // Matching the expression, not the name: an expression whitelist's name
    // rarely says what it actually matches.
    await user.type(box, "uptime");

    expect(screen.getByText("monitoring")).toBeInTheDocument();
    expect(screen.queryByText("office")).not.toBeInTheDocument();
  });
});

describe("SecurityView decision search", () => {
  beforeEach(() => {
    vi.spyOn(crowdsec, "health").mockResolvedValue(healthOk as never);
    vi.spyOn(crowdsec, "listAlerts").mockResolvedValue(emptyAlerts as never);
    vi.spyOn(crowdsec, "listWhitelists").mockResolvedValue([] as never);
    vi.spyOn(crowdsec, "whitelistStatus").mockResolvedValue({
      ok: true,
      error: null,
      applied_at: null,
      reload_configured: true,
    } as never);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("sends the query to the server and resets to page 1", async () => {
    // Server-side because a client-side filter here would search only the
    // visible page. Page 1 because filtering while on page 4 otherwise lands
    // past the end of a shorter result set.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const listDecisions = vi
      .spyOn(crowdsec, "listDecisions")
      .mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 } as never);

    render(<SecurityView />);
    await user.click(await screen.findByRole("tab", { name: /active decisions/i }));
    const box = await screen.findByRole("searchbox", { name: "Search decisions" });

    await user.type(box, "203.0");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });

    expect(listDecisions.mock.calls.at(-1)?.[0]).toMatchObject({ q: "203.0", page: 1 });
  });

  it("says a search is hiding the decisions, not that there are none", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    vi.spyOn(crowdsec, "listDecisions").mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 50,
    } as never);

    render(<SecurityView />);
    await user.click(await screen.findByRole("tab", { name: /active decisions/i }));
    await user.type(
      await screen.findByRole("searchbox", { name: "Search decisions" }),
      "203.0",
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });

    expect(screen.getByText(/no decisions match/i)).toBeInTheDocument();
    expect(screen.queryByText(/no active decisions/i)).not.toBeInTheDocument();
  });
});

describe("SecurityView alert search", () => {
  beforeEach(() => {
    vi.spyOn(crowdsec, "health").mockResolvedValue(healthOk as never);
    vi.spyOn(crowdsec, "listDecisions").mockResolvedValue(decisionList(120) as never);
    vi.spyOn(crowdsec, "listWhitelists").mockResolvedValue([] as never);
    vi.spyOn(crowdsec, "whitelistStatus").mockResolvedValue({
      ok: true,
      error: null,
      applied_at: null,
      reload_configured: true,
    } as never);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("sends the query to the server and resets to page 1", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const listAlerts = vi
      .spyOn(crowdsec, "listAlerts")
      .mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 } as never);

    render(<SecurityView />);
    await user.click(await screen.findByRole("tab", { name: /recent alerts/i }));
    await user.type(await screen.findByRole("searchbox", { name: "Search alerts" }), "ssh");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });

    expect(listAlerts.mock.calls.at(-1)?.[0]).toMatchObject({ q: "ssh", page: 1 });
  });
});
