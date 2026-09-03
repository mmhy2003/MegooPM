import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import {
  customPages,
  instanceSettings,
  type CustomPageSummary,
  type InstanceSettings,
} from "@/lib/api";
import { SettingsView } from "@/components/settings/settings-view";

const STAMPS = { created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z" };

function makeSettings(overrides: Partial<InstanceSettings> = {}): InstanceSettings {
  return {
    default_site_mode: "not_found",
    crowdsec_ban_mode: "megoopm",
    crowdsec_ban_page_id: null,
    default_site_redirect_url: null,
    default_site_page_id: null,
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
    updated_at: STAMPS.updated_at,
    ...overrides,
  };
}

const PAGE: CustomPageSummary = {
  id: 5,
  name: "Access denied",
  description: "",
  size_bytes: 120,
  ...STAMPS,
};

describe("SettingsView", () => {
  beforeEach(() => {
    push.mockClear();
    vi.spyOn(instanceSettings, "get").mockResolvedValue(makeSettings());
    vi.spyOn(instanceSettings, "updateDefaultSite").mockResolvedValue(makeSettings());
    vi.spyOn(customPages, "list").mockResolvedValue([PAGE]);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("offers all five modes with the saved one selected", async () => {
    render(<SettingsView />);
    expect(await screen.findByRole("radio", { name: /404 page/i })).toBeChecked();
    for (const name of [
      /Congratulations page/i,
      /404 page/i,
      /No response/i,
      /Redirect/i,
      /Custom page/i,
    ]) {
      const group = screen.getByRole("radiogroup", { name: "Default site" });
      expect(within(group).getByRole("radio", { name })).toBeInTheDocument();
    }
  });

  it("reveals the URL field only for Redirect", async () => {
    const user = userEvent.setup();
    render(<SettingsView />);
    await screen.findByRole("radio", { name: /404 page/i });
    expect(screen.queryByLabelText("Redirect to")).not.toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: /Redirect/i }));
    expect(screen.getByLabelText("Redirect to")).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: /No response/i }));
    expect(screen.queryByLabelText("Redirect to")).not.toBeInTheDocument();
  });

  it("reveals the page picker only for Custom page", async () => {
    const user = userEvent.setup();
    render(<SettingsView />);
    await screen.findByRole("radio", { name: /404 page/i });
    expect(screen.queryByLabelText("Page to serve")).not.toBeInTheDocument();

    // Two cards now offer a "Custom page" radio; scope to the one meant.
    const group = screen.getByRole("radiogroup", { name: "Default site" });
    await user.click(within(group).getByRole("radio", { name: /Custom page/i }));
    expect(await screen.findByLabelText("Page to serve")).toBeInTheDocument();
  });

  it("saves a simple mode", async () => {
    const user = userEvent.setup();
    render(<SettingsView />);
    await user.click(await screen.findByRole("radio", { name: /No response/i }));
    await user.click(screen.getByRole("button", { name: "Save default site" }));

    await waitFor(() => expect(instanceSettings.updateDefaultSite).toHaveBeenCalledTimes(1));
    expect(instanceSettings.updateDefaultSite).toHaveBeenCalledWith({
      default_site_mode: "no_response",
      default_site_redirect_url: null,
      default_site_page_id: null,
    });
  });

  it("saves a redirect with its URL", async () => {
    const user = userEvent.setup();
    render(<SettingsView />);
    await user.click(await screen.findByRole("radio", { name: /Redirect/i }));
    await user.type(screen.getByLabelText("Redirect to"), "https://example.com");
    await user.click(screen.getByRole("button", { name: "Save default site" }));

    await waitFor(() => expect(instanceSettings.updateDefaultSite).toHaveBeenCalledTimes(1));
    expect(vi.mocked(instanceSettings.updateDefaultSite).mock.calls[0][0]).toMatchObject({
      default_site_mode: "redirect",
      default_site_redirect_url: "https://example.com",
    });
  });

  it("blocks a redirect with no URL and says why", async () => {
    const user = userEvent.setup();
    render(<SettingsView />);
    await user.click(await screen.findByRole("radio", { name: /Redirect/i }));
    await user.click(screen.getByRole("button", { name: "Save default site" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Enter the URL to redirect to.");
    expect(instanceSettings.updateDefaultSite).not.toHaveBeenCalled();
  });

  it("points at Custom Pages when there are none to choose", async () => {
    vi.mocked(customPages.list).mockResolvedValue([]);
    const user = userEvent.setup();
    render(<SettingsView />);
    const group = await screen.findByRole("radiogroup", { name: "Default site" });
    await user.click(within(group).getByRole("radio", { name: /Custom page/i }));

    expect(await screen.findByText(/no custom pages/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Create a page/i })).toBeInTheDocument();
    expect(screen.queryByLabelText("Page to serve")).not.toBeInTheDocument();
  });

  it("keeps Save disabled until something changes", async () => {
    const user = userEvent.setup();
    render(<SettingsView />);
    await screen.findByRole("radio", { name: /404 page/i });
    expect(screen.getByRole("button", { name: "Save default site" })).toBeDisabled();
    await user.click(screen.getByRole("radio", { name: /Redirect/i }));
    expect(screen.getByRole("button", { name: "Save default site" })).toBeEnabled();
  });

  it("surfaces a load failure with a retry", async () => {
    vi.mocked(instanceSettings.get).mockRejectedValueOnce(new Error("boom"));
    render(<SettingsView />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn't load/i);
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
