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

  it("will not save the custom-page mode with no page chosen", async () => {
    // The API rejects it with a 422; refusing here is the better error.
    const user = userEvent.setup();
    renderCard(makeSettings(), []);

    await user.click(screen.getByRole("radio", { name: new RegExp("^Custom page") }));

    expect(screen.getByRole("button", { name: "Save ban page" })).toBeDisabled();
  });
});
