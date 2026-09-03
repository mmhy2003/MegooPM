import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { instanceSettings, type InstanceSettings } from "@/lib/api";
import { LlmCard } from "@/components/settings/llm-card";

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
    updated_at: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

function renderCard(settings = makeSettings()) {
  return render(<LlmCard settings={settings} onSaved={() => {}} />);
}

describe("LlmCard", () => {
  beforeEach(() => {
    vi.spyOn(instanceSettings, "updateLlm").mockResolvedValue(makeSettings());
    vi.spyOn(instanceSettings, "testLlm").mockResolvedValue({
      ok: true,
      model: "gpt-4o",
      reply: "OK",
      error: "",
      latency_ms: 412,
    });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("starts disabled on a fresh instance", () => {
    renderCard();
    expect(screen.getByLabelText("Enable LLM features")).toHaveAttribute("aria-checked", "false");
  });

  it("never prefills the key, and says whether one is stored", () => {
    renderCard(makeSettings({ llm_api_key_set: true, llm_model: "gpt-4o" }));
    expect(screen.getByLabelText("API key")).toHaveValue("");
    expect(screen.getByText(/a key is stored/i)).toBeInTheDocument();
  });

  it("says the model needs a provider prefix, and what to use for a custom base", () => {
    // Without this an operator points api_base at an OpenAI-compatible endpoint,
    // enters the provider's bare model name, and gets litellm's
    // "LLM Provider NOT provided" with nothing on screen explaining it.
    renderCard();
    expect(screen.getByText(/provider prefix is always required/i)).toBeInTheDocument();
    expect(screen.getByText("openai/gpt-4o")).toBeInTheDocument();
    expect(screen.getByText(/openai-compatible endpoint/i)).toBeInTheDocument();
  });

  it("says when no key is stored", () => {
    renderCard();
    expect(screen.getByText(/no key stored/i)).toBeInTheDocument();
  });

  it("saves the group without a key when the key was not retyped", async () => {
    const user = userEvent.setup();
    renderCard(makeSettings({ llm_enabled: true, llm_model: "gpt-4o", llm_api_key_set: true }));

    await user.clear(screen.getByLabelText("Model"));
    await user.type(screen.getByLabelText("Model"), "gpt-4o-mini");
    await user.click(screen.getByRole("button", { name: "Save LLM settings" }));

    await waitFor(() => expect(instanceSettings.updateLlm).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(instanceSettings.updateLlm).mock.calls[0][0];
    expect(payload.llm_model).toBe("gpt-4o-mini");
    expect("llm_api_key" in payload).toBe(false);
  });

  it("sends a retyped key", async () => {
    const user = userEvent.setup();
    renderCard(makeSettings({ llm_enabled: true, llm_model: "gpt-4o" }));

    await user.type(screen.getByLabelText("API key"), "sk-brand-new");
    await user.click(screen.getByRole("button", { name: "Save LLM settings" }));

    await waitFor(() => expect(instanceSettings.updateLlm).toHaveBeenCalledTimes(1));
    expect(vi.mocked(instanceSettings.updateLlm).mock.calls[0][0].llm_api_key).toBe("sk-brand-new");
  });

  it("clears a stored key on demand", async () => {
    const user = userEvent.setup();
    renderCard(makeSettings({ llm_enabled: true, llm_model: "gpt-4o", llm_api_key_set: true }));

    await user.click(screen.getByRole("button", { name: "Remove stored key" }));
    await user.click(screen.getByRole("button", { name: "Save LLM settings" }));

    await waitFor(() => expect(instanceSettings.updateLlm).toHaveBeenCalledTimes(1));
    expect(vi.mocked(instanceSettings.updateLlm).mock.calls[0][0].llm_api_key).toBeNull();
  });

  it("blocks enabling with no model and says why", async () => {
    const user = userEvent.setup();
    renderCard();
    await user.click(screen.getByLabelText("Enable LLM features"));
    await user.click(screen.getByRole("button", { name: "Save LLM settings" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter a model to enable LLM features.",
    );
    expect(instanceSettings.updateLlm).not.toHaveBeenCalled();
  });

  it("shows the reply and round trip on a successful probe", async () => {
    const user = userEvent.setup();
    renderCard(makeSettings({ llm_model: "gpt-4o" }));

    await user.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText(/Connected/)).toBeInTheDocument();
    expect(screen.getByText(/412/)).toBeInTheDocument();
  });

  it("shows the error when the probe fails, without treating it as a crash", async () => {
    vi.mocked(instanceSettings.testLlm).mockResolvedValue({
      ok: false,
      model: "gpt-4o",
      reply: "",
      error: "401 unauthorized",
      latency_ms: 88,
    });
    const user = userEvent.setup();
    renderCard(makeSettings({ llm_model: "gpt-4o" }));

    await user.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("401 unauthorized");
  });

  it("tests what is in the form, so a key can be checked before saving", async () => {
    const user = userEvent.setup();
    renderCard(makeSettings({ llm_model: "gpt-4o" }));

    await user.type(screen.getByLabelText("API key"), "sk-unsaved");
    await user.click(screen.getByRole("button", { name: "Test connection" }));

    await waitFor(() => expect(instanceSettings.testLlm).toHaveBeenCalledTimes(1));
    expect(vi.mocked(instanceSettings.testLlm).mock.calls[0][0]).toEqual({
      model: "gpt-4o",
      api_key: "sk-unsaved",
    });
  });

  it("can test while the feature is switched off", async () => {
    const user = userEvent.setup();
    renderCard(makeSettings({ llm_enabled: false, llm_model: "gpt-4o" }));
    expect(screen.getByRole("button", { name: "Test connection" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Test connection" }));
    await waitFor(() => expect(instanceSettings.testLlm).toHaveBeenCalledTimes(1));
  });
});
