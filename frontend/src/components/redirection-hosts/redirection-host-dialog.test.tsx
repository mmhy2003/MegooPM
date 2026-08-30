import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { redirectionHosts, type RedirectionHost } from "@/lib/api";
import { RedirectionHostDialog } from "@/components/redirection-hosts/redirection-host-dialog";

function makeRedirect(over: Partial<RedirectionHost> = {}): RedirectionHost {
  return {
    id: 1,
    domain_names: ["old.example.com"],
    forward_domain_name: "new.example.com",
    forward_scheme: "auto",
    forward_http_code: 302,
    preserve_path: true,
    certificate_id: null,
    enabled: true,
    ssl_forced: false,
    http2_support: false,
    hsts_enabled: false,
    hsts_subdomains: false,
    block_exploits: false,
    advanced_config: "",
    created_at: "2026-08-30T00:00:00Z",
    updated_at: "2026-08-30T00:00:00Z",
    ...over,
  };
}

function renderDialog(host: RedirectionHost | null = makeRedirect()) {
  return render(
    <RedirectionHostDialog
      open
      onOpenChange={() => {}}
      host={host}
      certificates={[]}
      onSaved={() => {}}
    />,
  );
}

describe("RedirectionHostDialog", () => {
  beforeEach(() => {
    vi.spyOn(redirectionHosts, "update").mockResolvedValue(makeRedirect());
    vi.spyOn(redirectionHosts, "create").mockResolvedValue(makeRedirect());
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("splits the form into Details and SSL tabs, with domains outside them", () => {
    renderDialog();
    // Domains identify the host, so they stay above the tab strip.
    expect(screen.getByLabelText("Domain names")).toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Details", "SSL"]);
  });

  it("shows the non-SSL fields on Details", () => {
    renderDialog();
    expect(screen.getByLabelText("Forward domain")).toBeInTheDocument();
    expect(screen.getByLabelText("Forward scheme")).toBeInTheDocument();
    expect(screen.getByLabelText("HTTP status code")).toBeInTheDocument();
    expect(screen.getByLabelText("Preserve path")).toBeInTheDocument();
    // Block exploits is a security option, not a TLS one — it stays on Details,
    // matching how the proxy-host dialog groups it away from TLS_TOGGLES.
    expect(screen.getByLabelText("Block exploits")).toBeInTheDocument();
    expect(screen.getByLabelText("Enabled")).toBeInTheDocument();
    // The SSL fields are not rendered while Details is active.
    expect(screen.queryByLabelText("Force SSL")).not.toBeInTheDocument();
  });

  it("moves the certificate and TLS toggles to the SSL tab", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("tab", { name: "SSL" }));
    expect(await screen.findByLabelText("SSL certificate")).toBeInTheDocument();
    for (const name of ["Force SSL", "HTTP/2", "HSTS", "HSTS subdomains"]) {
      expect(screen.getByLabelText(name)).toBeInTheDocument();
    }
    // Details fields are not duplicated onto the SSL tab.
    expect(screen.queryByLabelText("Forward domain")).not.toBeInTheDocument();
  });

  it("disables the TLS toggles while no certificate is selected", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("tab", { name: "SSL" }));
    await screen.findByLabelText("SSL certificate");
    // base-ui renders <span role="switch" aria-disabled>, not a disabled attribute.
    for (const name of ["Force SSL", "HTTP/2", "HSTS", "HSTS subdomains"]) {
      expect(screen.getByLabelText(name)).toHaveAttribute("aria-disabled", "true");
    }
  });

  it("enables the TLS toggles when the host has a certificate", async () => {
    const user = userEvent.setup();
    renderDialog(makeRedirect({ certificate_id: 7 }));
    await user.click(screen.getByRole("tab", { name: "SSL" }));
    expect(await screen.findByLabelText("Force SSL")).not.toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("jumps back to Details when validation fails on a hidden field", async () => {
    const user = userEvent.setup();
    renderDialog(makeRedirect({ forward_domain_name: "" }));
    await user.click(screen.getByRole("tab", { name: "SSL" }));
    await screen.findByLabelText("SSL certificate");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter a forward domain to redirect to.",
    );
    // Without this the operator sees an error about a field they cannot see.
    expect(screen.getByRole("tab", { name: "Details" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(redirectionHosts.update).not.toHaveBeenCalled();
  });

  it("submits the same payload as before the fields were split", async () => {
    const user = userEvent.setup();
    renderDialog(
      makeRedirect({
        certificate_id: 7,
        ssl_forced: true,
        hsts_enabled: true,
        block_exploits: true,
        forward_http_code: 301,
        forward_scheme: "https",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(redirectionHosts.update).toHaveBeenCalledTimes(1));
    // Tabbing is presentation only: the wire format must be untouched.
    expect(vi.mocked(redirectionHosts.update).mock.calls[0][1]).toMatchObject({
      domain_names: ["old.example.com"],
      forward_domain_name: "new.example.com",
      forward_scheme: "https",
      forward_http_code: 301,
      preserve_path: true,
      certificate_id: 7,
      enabled: true,
      ssl_forced: true,
      http2_support: false,
      hsts_enabled: true,
      hsts_subdomains: false,
      block_exploits: true,
    });
  });
});
