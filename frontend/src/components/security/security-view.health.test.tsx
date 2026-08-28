import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { crowdsec } from "@/lib/api";
import { SecurityView } from "@/components/security/security-view";

const empty = { total: 0, page: 1, page_size: 50, items: [] };

describe("SecurityView health banner", () => {
  beforeEach(() => {
    vi.spyOn(crowdsec, "listDecisions").mockResolvedValue(empty as never);
    vi.spyOn(crowdsec, "listAlerts").mockResolvedValue(empty as never);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("warns when LAPI is reachable but no machine is registered", async () => {
    vi.spyOn(crowdsec, "health").mockResolvedValue({
      configured: true,
      reachable: true,
      machine_registered: false,
      lapi_url: "http://lapi:8080",
      detail: "No LAPI machine is registered for this deployment yet.",
    } as never);
    render(<SecurityView />);
    expect(
      await screen.findByText("Connected to LAPI, but no machine is registered yet"),
    ).toBeInTheDocument();
    expect(screen.getByText(/No LAPI machine is registered/)).toBeInTheDocument();
  });

  it("shows the connected banner once the machine exists", async () => {
    vi.spyOn(crowdsec, "health").mockResolvedValue({
      configured: true,
      reachable: true,
      machine_registered: true,
      lapi_url: "http://lapi:8080",
      detail: null,
    } as never);
    render(<SecurityView />);
    expect(await screen.findByText(/Connected to LAPI at/)).toBeInTheDocument();
    expect(screen.queryByText(/no machine is registered/)).not.toBeInTheDocument();
  });
});
