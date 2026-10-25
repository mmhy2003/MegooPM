import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { crowdsec, instanceSettings, type CrowdSecWafStatus, type InstanceSettings } from "@/lib/api";
import { SettingsTab } from "@/components/security/settings-tab";

const IDLE: CrowdSecWafStatus = {
  max_body_mb: 10,
  body_exceeded_action: "partial",
  run: null,
  running: false,
  reload_configured: true,
  appsec_configured: true,
  protected_hosts: [],
  capi_enabled: false,
  capi_blocked: false,
};

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.spyOn(toast, "success").mockImplementation(() => "" as never);
  vi.spyOn(toast, "error").mockImplementation(() => "" as never);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("SettingsTab", () => {
  it("keeps polling after a save until the worker picks it up", async () => {
    // The reload right after Save can come before the worker takes the lock:
    // nothing reads as running, and without this the card would never update.
    const waf = vi.spyOn(crowdsec, "waf").mockResolvedValue(IDLE);
    vi.spyOn(instanceSettings, "updateCrowdSecWaf").mockResolvedValue({} as InstanceSettings);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<SettingsTab />);

    await user.click(await screen.findByRole("button", { name: /save and restart crowdsec/i }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /^save and restart$/i }));
    const afterSave = waf.mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(11_000);
    });

    expect(waf.mock.calls.length).toBeGreaterThanOrEqual(afterSave + 2);
  });

  it("stops polling once nothing is pending", async () => {
    const waf = vi.spyOn(crowdsec, "waf").mockResolvedValue(IDLE);
    render(<SettingsTab />);
    await screen.findByRole("button", { name: /save and restart crowdsec/i });
    const loaded = waf.mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(waf.mock.calls.length).toBe(loaded);
  });
});
