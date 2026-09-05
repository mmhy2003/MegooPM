import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { crowdsec, type Alert, type Decision } from "@/lib/api";
import { AlertDetailsDialog, DecisionDetailsDialog } from "@/components/security/details-dialog";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/** A fetch that never settles, for the tests that only care about the header. */
function pending() {
  vi.spyOn(crowdsec, "getAlert").mockReturnValue(new Promise(() => {}));
}

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

/** An alert as the *list* returns it: no context, no events. */
const ALERT_BASE = {
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
  decisions: [{ type: "ban", scope: "Ip", value: "203.0.113.7", duration: "4h" }],
  created_at: "2026-09-05T10:00:00Z",
  start_at: "2026-09-05T09:58:00Z",
  stop_at: "2026-09-05T10:00:00Z",
};

const ALERT = ALERT_BASE as Alert;

/** The same alert as the *detail* endpoint returns it. */
const INSPECTED = {
  ...ALERT_BASE,
  machine_id: "testMachine",
  uuid: "0061339c-f070-4859-8f2a-66249c709d73",
  simulated: false,
  meta: [{ key: "target_uri", value: "/lanz.php" }],
  events: [
    {
      timestamp: "2026-09-05T10:00:00Z",
      meta: [
        { key: "http_verb", value: "GET" },
        { key: "http_status", value: "404" },
      ],
    },
  ],
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

  it("shows the expiry LAPI sends, not only the duration", () => {
    // "3h59m59s" cannot answer "when does this lift?" hours after the fact.
    render(
      <DecisionDetailsDialog
        decision={{ ...DECISION, until: "2026-09-05T14:00:00Z" } as Decision}
        onOpenChange={vi.fn()}
      />,
    );
    expect(screen.getByText(/Expires/i)).toBeInTheDocument();
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
    pending();
    render(<AlertDetailsDialog alert={ALERT} onOpenChange={vi.fn()} />);

    // The table truncates the message and never renders these at all.
    expect(screen.getByText(/performed 'crowdsecurity\/http-probing'/)).toBeInTheDocument();
    expect(screen.getByText("Example Transit AG")).toBeInTheDocument();
    expect(screen.getByText(/52\.52/)).toBeInTheDocument();
  });

  it("lists the decisions the alert triggered", () => {
    // Fetched with every alert today and thrown away by the table.
    pending();
    render(<AlertDetailsDialog alert={ALERT} onOpenChange={vi.fn()} />);
    expect(screen.getByText(/ban/)).toBeInTheDocument();
    expect(screen.getByText(/4h/)).toBeInTheDocument();
  });

  it("says so when an alert triggered nothing", () => {
    pending();
    render(
      <AlertDetailsDialog
        alert={{ ...ALERT_BASE, decisions: [] } as Alert}
        onOpenChange={vi.fn()}
      />,
    );
    expect(screen.getByText(/no decisions/i)).toBeInTheDocument();
  });

  it("survives the decisions field being absent entirely", () => {
    // LAPI sends `decisions: null` for every AppSec detection; the generated
    // type keeps the field optional, so undefined has to be safe too.
    pending();
    render(
      <AlertDetailsDialog
        alert={{ ...ALERT_BASE, decisions: undefined } as unknown as Alert}
        onOpenChange={vi.fn()}
      />,
    );
    expect(screen.getByText(/no decisions/i)).toBeInTheDocument();
  });

  it("omits coordinates rather than printing a fake origin", () => {
    // CrowdSec's geoip parser fills these only sometimes; 0,0 is the Atlantic.
    pending();
    render(
      <AlertDetailsDialog
        alert={
          {
            ...ALERT_BASE,
            source: { ...ALERT_BASE.source, latitude: null, longitude: null },
          } as Alert
        }
        onOpenChange={vi.fn()}
      />,
    );
    expect(screen.queryByText(/Coordinates/i)).not.toBeInTheDocument();
  });
});

describe("AlertDetailsDialog inspection", () => {
  it("shows the row's own fields before the fetch resolves", () => {
    // A dialog that blanks until the network answers reads as broken, and it
    // must stay useful when LAPI is down.
    pending();
    render(<AlertDetailsDialog alert={ALERT} onOpenChange={vi.fn()} />);

    expect(screen.getByText("Example Transit AG")).toBeInTheDocument();
  });

  it("fetches the alert and shows its context and events", async () => {
    const get = vi.spyOn(crowdsec, "getAlert").mockResolvedValue(INSPECTED);
    render(<AlertDetailsDialog alert={ALERT} onOpenChange={vi.fn()} />);

    await waitFor(() => expect(get).toHaveBeenCalledWith(9));
    // The Context table cscli prints, and one event's parsed fields.
    expect(await screen.findByText("target_uri")).toBeInTheDocument();
    expect(screen.getByText("/lanz.php")).toBeInTheDocument();
    expect(screen.getByText("http_status")).toBeInTheDocument();
    expect(screen.getByText("testMachine")).toBeInTheDocument();
  });

  it("keeps the header when the fetch fails", async () => {
    // LAPI being unreachable must not cost the operator what we already had.
    vi.spyOn(crowdsec, "getAlert").mockRejectedValue(new Error("LAPI is down"));
    render(<AlertDetailsDialog alert={ALERT} onOpenChange={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/LAPI is down/);
    expect(screen.getByText("Example Transit AG")).toBeInTheDocument();
  });

  it("does not fetch an alert with no id", () => {
    const get = vi.spyOn(crowdsec, "getAlert");
    render(
      <AlertDetailsDialog alert={{ ...ALERT_BASE, id: null } as Alert} onOpenChange={vi.fn()} />,
    );
    expect(get).not.toHaveBeenCalled();
  });
});
