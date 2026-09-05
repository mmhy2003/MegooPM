import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Alert, Decision } from "@/lib/api";
import { AlertDetailsDialog, DecisionDetailsDialog } from "@/components/security/details-dialog";

afterEach(cleanup);

const DECISION: Decision = {
  id: 42,
  origin: "crowdsec",
  type: "ban",
  scope: "Ip",
  value: "203.0.113.7",
  duration: "3h59m59s",
  scenario: "crowdsecurity/ssh-bf",
  country: "DE",
} as Decision;

const ALERT: Alert = {
  id: 9,
  scenario: "crowdsecurity/http-probing",
  message: "Ip 203.0.113.7 performed 'crowdsecurity/http-probing' (12 events)",
  events_count: 12,
  source: {
    scope: "Ip",
    value: "203.0.113.7",
    ip: "203.0.113.7",
    cn: "DE",
    as_name: "Example Transit AG",
    latitude: 52.52,
    longitude: 13.4,
  },
  decisions: [{ type: "ban", scope: "Ip", value: "203.0.113.7", duration: "4h" } as Decision],
  created_at: "2026-09-05T10:00:00Z",
  start_at: "2026-09-05T09:58:00Z",
  stop_at: "2026-09-05T10:00:00Z",
} as Alert;

describe("DecisionDetailsDialog", () => {
  it("names the decision it is showing", () => {
    render(<DecisionDetailsDialog decision={DECISION} onOpenChange={vi.fn()} />);
    expect(screen.getByRole("heading", { name: /203\.0\.113\.7/ })).toBeInTheDocument();
  });

  it("shows the fields the table has room for and the ones it does not", () => {
    render(<DecisionDetailsDialog decision={DECISION} onOpenChange={vi.fn()} />);
    expect(screen.getByText("crowdsecurity/ssh-bf")).toBeInTheDocument();
    expect(screen.getByText("3h59m59s")).toBeInTheDocument();
    expect(screen.getByText("crowdsec")).toBeInTheDocument();
    // The id is what you quote to support; the table has no column for it.
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("shows an em dash where CrowdSec sent nothing", () => {
    // A blank cell reads as a rendering bug; "—" reads as "not sent".
    render(
      <DecisionDetailsDialog
        decision={{ ...DECISION, scenario: null, origin: null } as Decision}
        onOpenChange={vi.fn()}
      />,
    );
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });

  it("closes on Escape", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(<DecisionDetailsDialog decision={DECISION} onOpenChange={onOpenChange} />);
    await user.keyboard("{Escape}");
    // base-ui passes (open, eventDetails); only the first is ours.
    expect(onOpenChange.mock.calls[0][0]).toBe(false);
  });
});

describe("AlertDetailsDialog", () => {
  it("shows what the row truncates or omits entirely", () => {
    render(<AlertDetailsDialog alert={ALERT} onOpenChange={vi.fn()} />);

    // The table truncates the message and never renders these at all.
    expect(screen.getByText(/performed 'crowdsecurity\/http-probing'/)).toBeInTheDocument();
    expect(screen.getByText("Example Transit AG")).toBeInTheDocument();
    expect(screen.getByText(/52\.52/)).toBeInTheDocument();
  });

  it("lists the decisions the alert triggered", () => {
    // Fetched with every alert today and thrown away by the table.
    render(<AlertDetailsDialog alert={ALERT} onOpenChange={vi.fn()} />);
    expect(screen.getByText(/ban/)).toBeInTheDocument();
    expect(screen.getByText(/4h/)).toBeInTheDocument();
  });

  it("says so when an alert triggered nothing", () => {
    render(
      <AlertDetailsDialog alert={{ ...ALERT, decisions: [] } as Alert} onOpenChange={vi.fn()} />,
    );
    expect(screen.getByText(/no decisions/i)).toBeInTheDocument();
  });

  it("survives the decisions field being absent entirely", () => {
    // LAPI sends `decisions: null` for every AppSec detection; the generated
    // type keeps the field optional, so undefined has to be safe too.
    render(
      <AlertDetailsDialog
        alert={{ ...ALERT, decisions: undefined } as unknown as Alert}
        onOpenChange={vi.fn()}
      />,
    );
    expect(screen.getByText(/no decisions/i)).toBeInTheDocument();
  });

  it("omits coordinates rather than printing a fake origin", () => {
    // CrowdSec's geoip parser fills these only sometimes; 0,0 is the Atlantic.
    render(
      <AlertDetailsDialog
        alert={{ ...ALERT, source: { ...ALERT.source, latitude: null, longitude: null } } as Alert}
        onOpenChange={vi.fn()}
      />,
    );
    expect(screen.queryByText(/Coordinates/i)).not.toBeInTheDocument();
  });
});
