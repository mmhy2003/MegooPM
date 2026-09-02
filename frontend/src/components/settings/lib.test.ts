import { describe, expect, it } from "vitest";

import {
  DEFAULT_SITE_MODES,
  DEFAULT_SITE_MODE_LABELS,
  buildDefaultSitePayload,
  emptyFormState,
  stateFromSettings,
  validateSettingsForm,
  buildLlmPayload,
  buildLlmTestPayload,
  emptyLlmState,
  llmStateFromSettings,
  validateLlmForm,
  type LlmFormState,
  type SettingsFormState,
} from "@/components/settings/lib";
import type { InstanceSettings } from "@/lib/api";

const SETTINGS: InstanceSettings = {
  default_site_mode: "redirect",
  crowdsec_ban_mode: "megoopm",
  crowdsec_ban_page_id: null,
  default_site_redirect_url: "https://example.com",
  default_site_page_id: null,
  llm_enabled: false,
  llm_model: null,
  llm_api_base: null,
  llm_api_key_set: false,
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

/* -------------------------------------------------------------------------- */
/* LLM integration                                                             */
/* -------------------------------------------------------------------------- */

const LLM_SETTINGS: InstanceSettings = {
  ...SETTINGS,
  llm_enabled: true,
  llm_model: "gpt-4o",
  llm_api_base: "https://gw.example.com",
  llm_api_key_set: true,
};

function llm(overrides: Partial<LlmFormState> = {}): LlmFormState {
  return { ...emptyLlmState(), ...overrides };
}

describe("llmStateFromSettings", () => {
  it("seeds from the server row without a key it can never read", () => {
    expect(llmStateFromSettings(LLM_SETTINGS)).toEqual({
      enabled: true,
      model: "gpt-4o",
      apiBase: "https://gw.example.com",
      apiKey: "",
      keyIsSet: true,
    });
  });

  it("turns nulls into empty strings so the inputs stay controlled", () => {
    const seeded = llmStateFromSettings({
      ...LLM_SETTINGS,
      llm_model: null,
      llm_api_base: null,
      llm_api_key_set: false,
    });
    expect(seeded.model).toBe("");
    expect(seeded.apiBase).toBe("");
    expect(seeded.keyIsSet).toBe(false);
  });
});

describe("validateLlmForm", () => {
  it("passes while disabled, whatever else is blank", () => {
    expect(validateLlmForm(llm({ enabled: false }))).toBeNull();
  });

  it("requires a model to enable", () => {
    expect(validateLlmForm(llm({ enabled: true, model: "  " }))).toBe(
      "Enter a model to enable LLM features.",
    );
  });

  it("does not require a key — local models have none", () => {
    expect(validateLlmForm(llm({ enabled: true, model: "ollama/llama3" }))).toBeNull();
  });
});

describe("buildLlmPayload", () => {
  it("omits the key entirely when it was not retyped", () => {
    const payload = buildLlmPayload(
      llm({ enabled: true, model: "gpt-4o", apiKey: "", keyIsSet: true }),
    );
    expect("llm_api_key" in payload).toBe(false);
    expect(payload).toEqual({
      llm_enabled: true,
      llm_model: "gpt-4o",
      llm_api_base: null,
    });
  });

  it("sends a retyped key", () => {
    const payload = buildLlmPayload(
      llm({ enabled: true, model: "gpt-4o", apiKey: "  sk-new  ", keyIsSet: true }),
    );
    expect(payload.llm_api_key).toBe("sk-new");
  });

  it("sends an explicit null when the key is cleared", () => {
    const payload = buildLlmPayload(
      llm({ enabled: true, model: "gpt-4o", apiKey: "", keyIsSet: false, keyCleared: true }),
    );
    expect(payload.llm_api_key).toBeNull();
  });
});

describe("buildLlmTestPayload", () => {
  it("sends only what the form actually holds, so stored values fill the rest", () => {
    expect(buildLlmTestPayload(llm({ model: "gpt-4o", apiBase: "", apiKey: "" }))).toEqual({
      model: "gpt-4o",
    });
  });

  it("includes a typed key so it can be checked before saving", () => {
    expect(buildLlmTestPayload(llm({ model: "gpt-4o", apiKey: "sk-typed" }))).toEqual({
      model: "gpt-4o",
      api_key: "sk-typed",
    });
  });
});
