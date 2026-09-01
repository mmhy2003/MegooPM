import { describe, expect, it } from "vitest";

import {
  DEFAULT_SITE_MODES,
  DEFAULT_SITE_MODE_LABELS,
  buildDefaultSitePayload,
  emptyFormState,
  stateFromSettings,
  validateSettingsForm,
  type SettingsFormState,
} from "@/components/settings/lib";
import type { InstanceSettings } from "@/lib/api";

const SETTINGS: InstanceSettings = {
  default_site_mode: "redirect",
  default_site_redirect_url: "https://example.com",
  default_site_page_id: null,
  updated_at: "2026-09-01T00:00:00Z",
};

function state(overrides: Partial<SettingsFormState> = {}): SettingsFormState {
  return { ...emptyFormState(), ...overrides };
}

describe("DEFAULT_SITE_MODES", () => {
  it("lists the five modes in the order the radio group shows them", () => {
    expect(DEFAULT_SITE_MODES).toEqual([
      "congratulations",
      "not_found",
      "no_response",
      "redirect",
      "custom_page",
    ]);
  });

  it("labels every mode", () => {
    for (const mode of DEFAULT_SITE_MODES) {
      expect(DEFAULT_SITE_MODE_LABELS[mode]).toBeTruthy();
    }
  });
});

describe("stateFromSettings", () => {
  it("seeds from the server row", () => {
    expect(stateFromSettings(SETTINGS)).toEqual({
      mode: "redirect",
      redirectUrl: "https://example.com",
      pageId: null,
    });
  });

  it("turns a null url into an empty string so the input stays controlled", () => {
    const seeded = stateFromSettings({ ...SETTINGS, default_site_redirect_url: null });
    expect(seeded.redirectUrl).toBe("");
  });
});

describe("validateSettingsForm", () => {
  it("passes the modes that need nothing else", () => {
    for (const mode of ["congratulations", "not_found", "no_response"] as const) {
      expect(validateSettingsForm(state({ mode }))).toBeNull();
    }
  });

  it("requires a url for redirect", () => {
    expect(validateSettingsForm(state({ mode: "redirect", redirectUrl: "  " }))).toBe(
      "Enter the URL to redirect to.",
    );
  });

  it("requires an absolute http(s) url", () => {
    expect(validateSettingsForm(state({ mode: "redirect", redirectUrl: "example.com" }))).toBe(
      "The URL must start with http:// or https://.",
    );
  });

  it("accepts a valid url", () => {
    expect(
      validateSettingsForm(state({ mode: "redirect", redirectUrl: "https://example.com/x" })),
    ).toBeNull();
  });

  it("requires a page for custom_page", () => {
    expect(validateSettingsForm(state({ mode: "custom_page", pageId: null }))).toBe(
      "Choose a custom page to serve.",
    );
  });
});

describe("buildDefaultSitePayload", () => {
  it("sends only the field the chosen mode uses", () => {
    expect(
      buildDefaultSitePayload(
        state({ mode: "redirect", redirectUrl: "  https://example.com  ", pageId: 4 }),
      ),
    ).toEqual({
      default_site_mode: "redirect",
      default_site_redirect_url: "https://example.com",
      default_site_page_id: null,
    });
  });

  it("nulls both extras for a simple mode", () => {
    expect(
      buildDefaultSitePayload(
        state({ mode: "not_found", redirectUrl: "https://x.com", pageId: 4 }),
      ),
    ).toEqual({
      default_site_mode: "not_found",
      default_site_redirect_url: null,
      default_site_page_id: null,
    });
  });

  it("sends the page for custom_page", () => {
    expect(buildDefaultSitePayload(state({ mode: "custom_page", pageId: 7 }))).toEqual({
      default_site_mode: "custom_page",
      default_site_redirect_url: null,
      default_site_page_id: 7,
    });
  });
});
