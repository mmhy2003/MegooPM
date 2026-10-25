import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { instanceSettings, type CrowdSecWafStatus, type InstanceSettings } from "@/lib/api";
import { WafSettingsCard } from "@/components/security/waf-settings-card";

function status(over: Partial<CrowdSecWafStatus> = {}): CrowdSecWafStatus {
  return {
    max_body_mb: 100,
    body_exceeded_action: "partial",
    run: null,
    running: false,
    reload_configured: true,
    appsec_configured: true,
    protected_hosts: [
      { kind: "proxy", id: 1, name: "cloud.example.com" },
      { kind: "dead", id: 2, name: "gone.example.com" },
    ],
    capi_enabled: false,
    capi_blocked: false,
    ...over,
  };
}

function renderCard(over: Partial<CrowdSecWafStatus> = {}, onChanged = vi.fn()) {
  render(<WafSettingsCard status={status(over)} onChanged={onChanged} />);
  return onChanged;
}

const SIZE = () => screen.getByRole("textbox", { name: /maximum body size/i });
const SAVE = () => screen.getByRole("button", { name: /save and restart crowdsec/i });

beforeEach(() => {
  vi.spyOn(toast, "success").mockImplementation(() => "" as never);
  vi.spyOn(toast, "error").mockImplementation(() => "" as never);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("WafSettingsCard", () => {
  it("lists the hosts the limit applies to", () => {
    renderCard();
    expect(screen.getByText("cloud.example.com")).toBeInTheDocument();
    expect(screen.getByText("gone.example.com")).toBeInTheDocument();
  });

  it("says so when no host uses CrowdSec", () => {
    renderCard({ protected_hosts: [] });
    expect(screen.getByText(/no hosts use crowdsec yet/i)).toBeInTheDocument();
  });

  it("offers the three choices with the approved titles", () => {
    renderCard();
    expect(screen.getByRole("radio", { name: /block it/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /check the first part/i })).toBeChecked();
    expect(
      screen.getByRole("radio", { name: /let it through without checking the body/i }),
    ).toBeInTheDocument();
  });

  it("shows the memory note above CrowdSec's default", async () => {
    // Measured on CrowdSec 1.8.1: checking 100 MB of one upload took ~800 MB,
    // while the 10 MB default stayed near 40 MB. Above the default, say so.
    const user = userEvent.setup();
    renderCard({ max_body_mb: 10 });
    expect(screen.queryByText(/several times that in memory/i)).not.toBeInTheDocument();

    await user.clear(SIZE());
    await user.type(SIZE(), "50");
    expect(screen.getByText(/several times that in memory/i)).toBeInTheDocument();
    expect(screen.getByText(/about 800 MB/i)).toBeInTheDocument();
  });

  it("will not save a size the API would refuse", async () => {
    const user = userEvent.setup();
    renderCard();
    await user.clear(SIZE());
    await user.type(SIZE(), "2049");
    expect(SAVE()).toBeDisabled();
    expect(screen.getByText(/between 1 MB and 2 GB/i)).toBeInTheDocument();
  });

  it("saves after the confirmation", async () => {
    const user = userEvent.setup();
    const save = vi
      .spyOn(instanceSettings, "updateCrowdSecWaf")
      .mockResolvedValue({} as InstanceSettings);
    const onChanged = renderCard();

    await user.click(screen.getByRole("radio", { name: /block it/i }));
    await user.click(SAVE());
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/restart crowdsec to apply/i)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: /^save and restart$/i }));

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith({ max_body_mb: 100, body_exceeded_action: "drop" }),
    );
    expect(onChanged).toHaveBeenCalled();
  });

  it("warns that the blocklist may be switched off only when it is blocked", async () => {
    const user = userEvent.setup();
    renderCard({ capi_enabled: true, capi_blocked: true });
    await user.click(SAVE());
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/may switch the community blocklist off/i)).toBeInTheDocument();

    cleanup();
    renderCard({ capi_enabled: true, capi_blocked: false });
    await user.click(SAVE());
    const plain = await screen.findByRole("dialog");
    expect(within(plain).queryByText(/community blocklist/i)).not.toBeInTheDocument();
  });

  it("says when AppSec is not wired", () => {
    renderCard({ appsec_configured: false });
    expect(screen.getByText(/appsec is off/i)).toBeInTheDocument();
  });

  it("cannot save while an apply runs", () => {
    renderCard({ running: true });
    expect(SAVE()).toBeDisabled();
  });
});
