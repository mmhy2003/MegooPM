import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { crowdsec, instanceSettings } from "@/lib/api";
import { ApiError } from "@/lib/api/errors";
import { UpdatesTab } from "@/components/security/updates-tab";

const SETTINGS = {
  default_site_mode: "not_found" as const,
  default_site_redirect_url: null,
  default_site_page_id: null,
  crowdsec_ban_mode: "megoopm" as const,
  crowdsec_ban_page_id: null,
  llm_enabled: false,
  llm_model: null,
  llm_api_base: null,
  llm_api_key_set: false,
  smtp_enabled: false,
  smtp_host: null,
  smtp_port: 587,
  smtp_security: "starttls" as const,
  smtp_username: null,
  smtp_password_set: false,
  smtp_from: null,
  smtp_from_name: null,
  app_url: null,
  crowdsec_hub_auto_update: true,
  crowdsec_hub_update_frequency: "daily" as const,
  crowdsec_hub_update_weekday: 6,
  crowdsec_hub_update_hour_utc: 3,
  crowdsec_capi_enabled: false,
  updated_at: "2026-09-04T00:00:00Z",
};
const EMPTY = {
  hub: null,
  capi: null,
  reload_configured: true,
  running: { hub: false, capi: false },
};

beforeEach(() => {
  vi.spyOn(instanceSettings, "get").mockResolvedValue(SETTINGS);
  vi.spyOn(crowdsec, "maintenance").mockResolvedValue(EMPTY);
  vi.spyOn(toast, "success").mockImplementation(() => "" as never);
  vi.spyOn(toast, "error").mockImplementation(() => "" as never);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("UpdatesTab schedule", () => {
  it("renders the schedule and disables Save until something changes", async () => {
    const user = userEvent.setup();
    render(<UpdatesTab />);
    const save = await screen.findByRole("button", { name: /save schedule/i });
    expect(save).toBeDisabled();
    await user.click(screen.getByRole("switch", { name: /update detection rules automatically/i }));
    expect(save).toBeEnabled();
  });

  it("saves the schedule in UTC", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(instanceSettings, "updateCrowdSecHub").mockResolvedValue(SETTINGS);
    render(<UpdatesTab />);
    await screen.findByRole("button", { name: /save schedule/i });
    await user.click(screen.getByRole("switch", { name: /update detection rules automatically/i }));
    await user.click(screen.getByRole("button", { name: /save schedule/i }));
    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0][0]).toMatchObject({
      auto_update: false,
      frequency: "daily",
      hour_utc: 3,
    });
  });
});

describe("UpdatesTab update now", () => {
  it("confirms with the fail-closed sentence, then queues", async () => {
    const user = userEvent.setup();
    const now = vi.spyOn(crowdsec, "hubUpdateNow").mockResolvedValue({ queued: true });
    render(<UpdatesTab />);
    await user.click(await screen.findByRole("button", { name: /update now/i }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(/deny traffic for a few seconds/i);
    await user.click(within(dialog).getByRole("button", { name: /^update now$/i }));
    await waitFor(() => expect(now).toHaveBeenCalled());
  });

  it("is disabled while a run is in progress", async () => {
    vi.mocked(crowdsec.maintenance).mockResolvedValue({
      ...EMPTY,
      running: { hub: true, capi: false },
    });
    render(<UpdatesTab />);
    expect(await screen.findByRole("button", { name: /running/i })).toBeDisabled();
  });

  it("shows the newer-agent note", async () => {
    vi.mocked(crowdsec.maintenance).mockResolvedValue({
      ...EMPTY,
      hub: {
        kind: "hub_update",
        started_at: "2026-09-04T03:05:00Z",
        finished_at: "2026-09-04T03:06:00Z",
        ok: true,
        error: null,
        trigger: "scheduled",
        restarted: false,
        detail: { updated: [], agent_version: "v1.6.4", latest_agent_version: "v1.8.0" },
      },
    });
    render(<UpdatesTab />);
    expect(await screen.findByText(/v1\.8\.0 is available/i)).toBeInTheDocument();
  });
});

describe("UpdatesTab blocklist", () => {
  it("confirms enabling with the registration sentence, then saves", async () => {
    const user = userEvent.setup();
    const update = vi
      .spyOn(instanceSettings, "updateCrowdSecCapi")
      .mockResolvedValue({ ...SETTINGS, crowdsec_capi_enabled: true });
    render(<UpdatesTab />);
    await user.click(await screen.findByRole("switch", { name: /community blocklist/i }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(/registers this instance/i);
    await user.click(within(dialog).getByRole("button", { name: /turn on/i }));
    await waitFor(() => expect(update).toHaveBeenCalledWith({ enabled: true }));
  });

  it("shows a failed apply with retry", async () => {
    const user = userEvent.setup();
    vi.mocked(instanceSettings.get).mockResolvedValue({
      ...SETTINGS,
      crowdsec_capi_enabled: true,
    });
    vi.mocked(crowdsec.maintenance).mockResolvedValue({
      ...EMPTY,
      capi: {
        kind: "capi_apply",
        started_at: "2026-09-04T03:05:00Z",
        finished_at: "2026-09-04T03:06:00Z",
        ok: false,
        error: "Registering with CrowdSec's central API failed: no route to host",
        trigger: "manual",
        restarted: false,
        detail: { enabled: false },
      },
    });
    const update = vi.spyOn(instanceSettings, "updateCrowdSecCapi").mockResolvedValue(SETTINGS);
    render(<UpdatesTab />);
    expect(await screen.findByText(/no route to host/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() => expect(update).toHaveBeenCalledWith({ enabled: true }));
  });

  it("explains itself when reloads are not configured", async () => {
    vi.mocked(crowdsec.maintenance).mockResolvedValue({ ...EMPTY, reload_configured: false });
    render(<UpdatesTab />);
    // base-ui renders the switch as a span with aria-disabled, not a disabled button.
    expect(await screen.findByRole("switch", { name: /community blocklist/i })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(screen.getByRole("button", { name: /update now/i })).toBeDisabled();
    expect(screen.getAllByText(/CROWDSEC_CONTROL_NODE_ID/).length).toBeGreaterThan(0);
  });

  it("surfaces a 409 from Update now", async () => {
    const user = userEvent.setup();
    vi.spyOn(crowdsec, "hubUpdateNow").mockRejectedValue(
      new ApiError(409, "Conflict", { detail: "An update is already running." }),
    );
    render(<UpdatesTab />);
    await user.click(await screen.findByRole("button", { name: /update now/i }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /^update now$/i }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("An update is already running."));
  });
});
