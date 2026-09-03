import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { instanceSettings, type InstanceSettings } from "@/lib/api";
import { SmtpCard } from "@/components/settings/smtp-card";

function makeSettings(over: Partial<InstanceSettings> = {}): InstanceSettings {
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
    updated_at: "2026-09-03T00:00:00Z",
    ...over,
  } as InstanceSettings;
}

const CONFIGURED = makeSettings({
  smtp_enabled: true,
  smtp_host: "mail.example.com",
  smtp_from: "megoopm@example.com",
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SmtpCard", () => {
  it("says no password is stored on a fresh instance", () => {
    render(<SmtpCard settings={makeSettings()} onSaved={() => {}} />);
    expect(screen.getByText(/no password stored/i)).toBeInTheDocument();
  });

  it("reports a stored password without showing it", () => {
    render(
      <SmtpCard
        settings={makeSettings({ smtp_password_set: true, smtp_host: "mail.example.com" })}
        onSaved={() => {}}
      />,
    );
    expect(screen.getByText(/a password is stored/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("refuses to save an enabled card with no host", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(instanceSettings, "updateSmtp");
    render(<SmtpCard settings={makeSettings()} onSaved={() => {}} />);

    await user.click(screen.getByRole("switch", { name: /send email/i }));
    await user.click(screen.getByRole("button", { name: /save email settings/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/host/i);
    expect(update).not.toHaveBeenCalled();
  });

  it("shows the failure detail when a test send fails", async () => {
    // The whole point of the button: the operator sees the real SMTP error.
    const user = userEvent.setup();
    vi.spyOn(instanceSettings, "testSmtp").mockResolvedValue({
      ok: false,
      detail: "SMTPAuthenticationError: bad credentials",
      latency_ms: 12,
    });
    render(<SmtpCard settings={CONFIGURED} onSaved={() => {}} />);

    await user.click(screen.getByRole("button", { name: /send test email/i }));

    expect(await screen.findByText(/bad credentials/i)).toBeInTheDocument();
  });

  it("confirms a successful test send", async () => {
    const user = userEvent.setup();
    vi.spyOn(instanceSettings, "testSmtp").mockResolvedValue({
      ok: true,
      detail: "Sent to ops@example.com.",
      latency_ms: 340,
    });
    render(<SmtpCard settings={CONFIGURED} onSaved={() => {}} />);

    await user.click(screen.getByRole("button", { name: /send test email/i }));

    expect(await screen.findByText(/sent to ops@example\.com/i)).toBeInTheDocument();
  });

  it("saves the whole card and reports the new settings upward", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(instanceSettings, "updateSmtp").mockResolvedValue(CONFIGURED);
    const onSaved = vi.fn();
    render(<SmtpCard settings={CONFIGURED} onSaved={onSaved} />);

    await user.click(screen.getByRole("button", { name: /save email settings/i }));

    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(onSaved).toHaveBeenCalledWith(CONFIGURED);
  });

  it("does not send the password when the field was left blank", async () => {
    // Sending null would wipe a working password every time the card is saved.
    const user = userEvent.setup();
    const update = vi.spyOn(instanceSettings, "updateSmtp").mockResolvedValue(CONFIGURED);
    render(
      <SmtpCard
        settings={makeSettings({
          smtp_enabled: true,
          smtp_host: "mail.example.com",
          smtp_from: "megoopm@example.com",
          smtp_password_set: true,
        })}
        onSaved={() => {}}
      />,
    );

    await user.click(screen.getByRole("button", { name: /save email settings/i }));

    await waitFor(() => expect(update).toHaveBeenCalled());
    expect("smtp_password" in update.mock.calls[0][0]).toBe(false);
  });
});
