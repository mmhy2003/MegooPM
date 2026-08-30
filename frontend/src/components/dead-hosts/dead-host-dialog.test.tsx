import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { deadHosts, type DeadHost } from "@/lib/api";
import { DeadHostDialog } from "@/components/dead-hosts/dead-host-dialog";

function makeDeadHost(over: Partial<DeadHost> = {}): DeadHost {
  return {
    id: 1,
    domain_names: ["parked.example.com"],
    certificate_id: null,
    enabled: true,
    ssl_forced: false,
    http2_support: false,
    hsts_enabled: false,
    hsts_subdomains: false,
    advanced_config: "",
    created_at: "2026-08-30T00:00:00Z",
    updated_at: "2026-08-30T00:00:00Z",
    ...over,
  };
}

function renderDialog(host: DeadHost | null = makeDeadHost()) {
  return render(
    <DeadHostDialog
      open
      onOpenChange={() => {}}
      host={host}
      certificates={[]}
      onSaved={() => {}}
    />,
  );
}

describe("DeadHostDialog", () => {
  beforeEach(() => {
    vi.spyOn(deadHosts, "update").mockResolvedValue(makeDeadHost());
    vi.spyOn(deadHosts, "create").mockResolvedValue(makeDeadHost());
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("splits the form into Details and SSL tabs", () => {
    renderDialog();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Details", "SSL"]);
  });

  it("keeps domains and Enabled on Details", () => {
    renderDialog();
    // A 404 host has nothing to configure but its domains, so unlike the
    // redirection dialog they live inside Details rather than above the tabs.
    expect(screen.getByLabelText("Domain names")).toBeInTheDocument();
    expect(screen.getByLabelText("Enabled")).toBeInTheDocument();
    expect(screen.queryByLabelText("Force SSL")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("SSL certificate")).not.toBeInTheDocument();
  });

  it("moves the certificate and TLS toggles to the SSL tab", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("tab", { name: "SSL" }));
    expect(await screen.findByLabelText("SSL certificate")).toBeInTheDocument();
    for (const name of ["Force SSL", "HTTP/2", "HSTS", "HSTS subdomains"]) {
      expect(screen.getByLabelText(name)).toBeInTheDocument();
    }
    expect(screen.queryByLabelText("Domain names")).not.toBeInTheDocument();
  });

  it("disables the TLS toggles while no certificate is selected", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("tab", { name: "SSL" }));
    await screen.findByLabelText("SSL certificate");
    for (const name of ["Force SSL", "HTTP/2", "HSTS", "HSTS subdomains"]) {
      expect(screen.getByLabelText(name)).toHaveAttribute("aria-disabled", "true");
    }
  });

  it("enables the TLS toggles when the host has a certificate", async () => {
    const user = userEvent.setup();
    renderDialog(makeDeadHost({ certificate_id: 3 }));
    await user.click(screen.getByRole("tab", { name: "SSL" }));
    expect(await screen.findByLabelText("Force SSL")).not.toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("jumps back to Details when validation fails on a hidden field", async () => {
    const user = userEvent.setup();
    renderDialog(makeDeadHost({ domain_names: [] }));
    await user.click(screen.getByRole("tab", { name: "SSL" }));
    await screen.findByLabelText("SSL certificate");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter at least one domain name.",
    );
    expect(screen.getByRole("tab", { name: "Details" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(deadHosts.update).not.toHaveBeenCalled();
  });

  it("submits the same payload as before the fields were split", async () => {
    const user = userEvent.setup();
    renderDialog(
      makeDeadHost({
        certificate_id: 3,
        ssl_forced: true,
        hsts_enabled: true,
        hsts_subdomains: true,
      }),
    );
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(deadHosts.update).toHaveBeenCalledTimes(1));
    expect(vi.mocked(deadHosts.update).mock.calls[0][1]).toMatchObject({
      domain_names: ["parked.example.com"],
      certificate_id: 3,
      enabled: true,
      ssl_forced: true,
      http2_support: false,
      hsts_enabled: true,
      hsts_subdomains: true,
    });
  });
});
