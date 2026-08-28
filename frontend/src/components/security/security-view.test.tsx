import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
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
      }),
    );
    expect(crowdsec.listAlerts).toHaveBeenCalledWith({
      page: 1,
      pageSize: 50,
      includeCommunity: false,
    });
  });

  it("advances the decisions page when Next is clicked", async () => {
    const user = userEvent.setup();
    render(<SecurityView />);

    const panel = await screen.findByRole("tabpanel");
    await waitFor(() => expect(crowdsec.listDecisions).toHaveBeenCalled());

    await user.click(within(panel).getByRole("button", { name: "Next page" }));

    await waitFor(() =>
      expect(crowdsec.listDecisions).toHaveBeenLastCalledWith({
        page: 2,
        pageSize: 50,
        includeCommunity: false,
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
      }),
    );
    expect(crowdsec.listAlerts).toHaveBeenLastCalledWith({
      page: 1,
      pageSize: 50,
      includeCommunity: true,
    });
  });

  it("re-fetches after an unban is confirmed", async () => {
    const user = userEvent.setup();
    render(<SecurityView />);
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
