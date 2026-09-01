import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
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
    default_site_redirect_url: null,
    default_site_page_id: null,
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
    vi.spyOn(instanceSettings, "update").mockResolvedValue(makeSettings());
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
      expect(screen.getByRole("radio", { name })).toBeInTheDocument();
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

    await user.click(screen.getByRole("radio", { name: /Custom page/i }));
    expect(await screen.findByLabelText("Page to serve")).toBeInTheDocument();
  });

  it("saves a simple mode", async () => {
    const user = userEvent.setup();
    render(<SettingsView />);
    await user.click(await screen.findByRole("radio", { name: /No response/i }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(instanceSettings.update).toHaveBeenCalledTimes(1));
    expect(instanceSettings.update).toHaveBeenCalledWith({
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
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(instanceSettings.update).toHaveBeenCalledTimes(1));
    expect(vi.mocked(instanceSettings.update).mock.calls[0][0]).toMatchObject({
      default_site_mode: "redirect",
      default_site_redirect_url: "https://example.com",
    });
  });

  it("blocks a redirect with no URL and says why", async () => {
    const user = userEvent.setup();
    render(<SettingsView />);
    await user.click(await screen.findByRole("radio", { name: /Redirect/i }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Enter the URL to redirect to.");
    expect(instanceSettings.update).not.toHaveBeenCalled();
  });

  it("points at Custom Pages when there are none to choose", async () => {
    vi.mocked(customPages.list).mockResolvedValue([]);
    const user = userEvent.setup();
    render(<SettingsView />);
    await user.click(await screen.findByRole("radio", { name: /Custom page/i }));

    expect(await screen.findByText(/no custom pages/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Create a page/i })).toBeInTheDocument();
    expect(screen.queryByLabelText("Page to serve")).not.toBeInTheDocument();
  });

  it("keeps Save disabled until something changes", async () => {
    const user = userEvent.setup();
    render(<SettingsView />);
    await screen.findByRole("radio", { name: /404 page/i });
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    await user.click(screen.getByRole("radio", { name: /Redirect/i }));
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
  });

  it("surfaces a load failure with a retry", async () => {
    vi.mocked(instanceSettings.get).mockRejectedValueOnce(new Error("boom"));
    render(<SettingsView />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn't load/i);
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
