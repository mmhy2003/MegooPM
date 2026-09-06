import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { instanceSettings, type CustomPageSummary, type InstanceSettings } from "@/lib/api";
import { MaintenanceCard } from "@/components/settings/maintenance-card";

function makeSettings(overrides: Partial<InstanceSettings> = {}): InstanceSettings {
  return {
    default_site_mode: "not_found",
    default_site_redirect_url: null,
    default_site_page_id: null,
    crowdsec_ban_mode: "megoopm",
    crowdsec_ban_page_id: null,
    maintenance_mode: "megoopm",
    maintenance_page_id: null,
    maintenance_retry_after_minutes: 60,
    llm_enabled: false,
    llm_model: null,
    llm_api_base: null,
    llm_api_key_set: false,
    smtp_enabled: false,
    smtp_host: null,
    smtp_port: 587,
    smtp_security: "starttls",
    smtp_username: null,
    smtp_password_set: false,
    smtp_from: null,
    smtp_from_name: null,
    crowdsec_hub_auto_update: true,
    crowdsec_hub_update_frequency: "daily" as const,
    crowdsec_hub_update_weekday: 6,
    crowdsec_hub_update_hour_utc: 3,
    crowdsec_capi_enabled: false,
    app_url: null,
    updated_at: "2026-09-06T00:00:00Z",
    ...overrides,
  };
}

const PAGE = {
  id: 4,
  name: "Back soon",
  description: null,
  html: "<h1>brb</h1>",
  created_at: "2026-09-06T00:00:00Z",
  updated_at: "2026-09-06T00:00:00Z",
} as unknown as CustomPageSummary;

function renderCard(settings = makeSettings(), pages: CustomPageSummary[] = [PAGE]) {
  return render(<MaintenanceCard settings={settings} pages={pages} onSaved={() => {}} />);
}

describe("MaintenanceCard", () => {
  beforeEach(() => {
    vi.spyOn(instanceSettings, "updateMaintenance").mockResolvedValue(makeSettings());
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("offers the page dropdown only for the custom-page mode", async () => {
    const user = userEvent.setup();
    renderCard();

    expect(screen.queryByLabelText("Page to serve")).not.toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: new RegExp("^Custom page") }));

    expect(await screen.findByLabelText("Page to serve")).toBeInTheDocument();
  });

  it("saves the mode, the page and the retry", async () => {
    const user = userEvent.setup();
    renderCard();

    await user.click(screen.getByRole("radio", { name: new RegExp("^Custom page") }));
    await user.click(screen.getByRole("combobox", { name: "Page to serve" }));
    await user.click(await screen.findByRole("option", { name: "Back soon" }));
    await user.click(screen.getByRole("button", { name: "Save maintenance page" }));

    expect(instanceSettings.updateMaintenance).toHaveBeenCalledWith({
      mode: "custom_page",
      page_id: 4,
      retry_after_minutes: 60,
    });
  });

  it("never sends a page the mode does not use", async () => {
    // The API answers 422 for exactly this: the payload would describe two
    // configurations at once.
    const user = userEvent.setup();
    renderCard(makeSettings({ maintenance_mode: "custom_page", maintenance_page_id: 4 }));

    await user.click(screen.getByRole("radio", { name: new RegExp("^MegooPM page") }));
    await user.click(screen.getByRole("button", { name: "Save maintenance page" }));

    expect(instanceSettings.updateMaintenance).toHaveBeenCalledWith({
      mode: "megoopm",
      page_id: null,
      retry_after_minutes: 60,
    });
  });

  it("has nothing to save until something changes", () => {
    renderCard();

    expect(screen.getByRole("button", { name: "Save maintenance page" })).toBeDisabled();
  });

  it("enables saving when only the retry changes", async () => {
    const user = userEvent.setup();
    renderCard();

    const field = screen.getByLabelText(/retry after/i);
    await user.clear(field);
    await user.type(field, "30");

    expect(screen.getByRole("button", { name: "Save maintenance page" })).toBeEnabled();
  });

  it("will not save the custom-page mode with no page chosen", async () => {
    // The API rejects it with a 422; refusing here is the better error.
    const user = userEvent.setup();
    renderCard(makeSettings(), []);

    await user.click(screen.getByRole("radio", { name: new RegExp("^Custom page") }));

    expect(screen.getByRole("button", { name: "Save maintenance page" })).toBeDisabled();
  });

  it("will not save a retry the API would reject", async () => {
    // ge=1 on the server; an empty or zero field must not reach it.
    const user = userEvent.setup();
    renderCard();

    const field = screen.getByLabelText(/retry after/i);
    await user.clear(field);
    await user.type(field, "0");

    expect(screen.getByRole("button", { name: "Save maintenance page" })).toBeDisabled();
  });

  it("shows the stored retry", () => {
    renderCard(makeSettings({ maintenance_retry_after_minutes: 45 }));

    expect(screen.getByLabelText(/retry after/i)).toHaveValue(45);
  });
});
