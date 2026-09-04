import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { instanceSettings, type CustomPageSummary, type InstanceSettings } from "@/lib/api";
import { BanPageCard } from "@/components/settings/ban-page-card";

function makeSettings(overrides: Partial<InstanceSettings> = {}): InstanceSettings {
  return {
    default_site_mode: "not_found",
    default_site_redirect_url: null,
    default_site_page_id: null,
    crowdsec_ban_mode: "megoopm",
    crowdsec_ban_page_id: null,
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
    updated_at: "2026-09-02T00:00:00Z",
    ...overrides,
  };
}

const PAGE = {
  id: 4,
  name: "Blocked",
  description: null,
  html: "<h1>no</h1>",
  created_at: "2026-09-02T00:00:00Z",
  updated_at: "2026-09-02T00:00:00Z",
} as unknown as CustomPageSummary;

function renderCard(settings = makeSettings(), pages: CustomPageSummary[] = [PAGE]) {
  return render(<BanPageCard settings={settings} pages={pages} onSaved={() => {}} />);
}

describe("BanPageCard", () => {
  beforeEach(() => {
    vi.spyOn(instanceSettings, "updateBanPage").mockResolvedValue(makeSettings());
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

  it("shows the chosen page's name, not its id", async () => {
    // Reported from a live stack: the trigger read "5". base-ui renders the
    // raw value unless the root is told the labels.
    renderCard({
      ...makeSettings(),
      crowdsec_ban_mode: "custom_page",
      crowdsec_ban_page_id: PAGE.id,
    });
    const trigger = await screen.findByLabelText("Page to serve");
    expect(trigger).toHaveTextContent("Blocked");
    expect(trigger).not.toHaveTextContent(/^4$/);
  });

  it("saves the chosen mode", async () => {
    const user = userEvent.setup();
    renderCard();

    await user.click(screen.getByRole("radio", { name: new RegExp("^No page") }));
    await user.click(screen.getByRole("button", { name: "Save ban page" }));

    expect(instanceSettings.updateBanPage).toHaveBeenCalledWith({
      crowdsec_ban_mode: "none",
      crowdsec_ban_page_id: null,
    });
  });

  it("clears a stale page reference when leaving the custom-page mode", async () => {
    // Sending it anyway would have the API store a page the mode does not use.
    const user = userEvent.setup();
    renderCard(makeSettings({ crowdsec_ban_mode: "custom_page", crowdsec_ban_page_id: 4 }));

    await user.click(screen.getByRole("radio", { name: new RegExp("^MegooPM page") }));
    await user.click(screen.getByRole("button", { name: "Save ban page" }));

    expect(instanceSettings.updateBanPage).toHaveBeenCalledWith({
      crowdsec_ban_mode: "megoopm",
      crowdsec_ban_page_id: null,
    });
  });

  it("says an edit to the chosen page needs a config change to take effect", async () => {
    // Otherwise an operator edits the page, sees no change, and assumes a bug.
    const user = userEvent.setup();
    renderCard();

    await user.click(screen.getByRole("radio", { name: new RegExp("^Custom page") }));

    expect(await screen.findByText(/takes effect on the next/i)).toBeInTheDocument();
  });

  it("has nothing to save until something changes", async () => {
    // A live button on an unchanged form invites a PATCH that writes back the
    // values already stored, and says nothing about whether an edit took.
    renderCard();

    expect(screen.getByRole("button", { name: "Save ban page" })).toBeDisabled();
  });

  it("enables saving once the mode differs from what is stored", async () => {
    const user = userEvent.setup();
    renderCard();

    await user.click(screen.getByRole("radio", { name: new RegExp("^No page") }));

    expect(screen.getByRole("button", { name: "Save ban page" })).toBeEnabled();
  });

  it("enables saving when only the chosen page differs", async () => {
    // The mode is unchanged here; the page underneath it is the whole edit.
    const user = userEvent.setup();
    renderCard(makeSettings({ crowdsec_ban_mode: "custom_page", crowdsec_ban_page_id: 4 }), [
      PAGE,
      { ...PAGE, id: 5, name: "Denied" } as CustomPageSummary,
    ]);
    expect(screen.getByRole("button", { name: "Save ban page" })).toBeDisabled();

    await user.click(screen.getByRole("combobox", { name: "Page to serve" }));
    await user.click(await screen.findByRole("option", { name: "Denied" }));

    expect(screen.getByRole("button", { name: "Save ban page" })).toBeEnabled();
  });

  it("goes back to having nothing to save when the choice is reverted", async () => {
    const user = userEvent.setup();
    renderCard();

    await user.click(screen.getByRole("radio", { name: new RegExp("^No page") }));
    await user.click(screen.getByRole("radio", { name: new RegExp("^MegooPM page") }));

    expect(screen.getByRole("button", { name: "Save ban page" })).toBeDisabled();
  });

  it("will not save the custom-page mode with no page chosen", async () => {
    // The API rejects it with a 422; refusing here is the better error.
    const user = userEvent.setup();
    renderCard(makeSettings(), []);

    await user.click(screen.getByRole("radio", { name: new RegExp("^Custom page") }));

    expect(screen.getByRole("button", { name: "Save ban page" })).toBeDisabled();
  });
});
