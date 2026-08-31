import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Certificate } from "@/lib/api";
import { CertificateSelect } from "@/components/hosts/certificate-select";

function cert(over: Partial<Certificate> = {}): Certificate {
  return {
    id: 7,
    name: "wildcard-cert",
    provider: "letsencrypt",
    status: "active",
    domain_names: ["*.example.com"],
    expires_on: "2027-01-01T00:00:00Z",
    meta: {},
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...over,
  } as Certificate;
}

afterEach(cleanup);

describe("CertificateSelect", () => {
  it("shows the certificate's name once selected, not its id", () => {
    render(
      <CertificateSelect
        id="cert"
        value="7"
        onValueChange={() => {}}
        certificates={[cert()]}
      />,
    );
    // base-ui renders the raw value in the trigger unless the root gets `items`,
    // which is how an operator ended up staring at "7".
    const trigger = screen.getByLabelText("SSL certificate");
    expect(trigger).toHaveTextContent("wildcard-cert (*.example.com)");
    expect(trigger).not.toHaveTextContent(/^7$/);
  });

  it("shows the none label when nothing is selected", () => {
    render(
      <CertificateSelect
        id="cert"
        value="none"
        onValueChange={() => {}}
        certificates={[cert()]}
        noneLabel="None (HTTP only)"
      />,
    );
    expect(screen.getByLabelText("SSL certificate")).toHaveTextContent("None (HTTP only)");
  });

  it("keeps the trigger in step with the option that was picked", async () => {
    const user = userEvent.setup();
    const onValueChange = vi.fn();
    const certs = [cert(), cert({ id: 9, name: "api-cert", domain_names: ["api.example.com"] })];
    render(
      <CertificateSelect id="cert" value="7" onValueChange={onValueChange} certificates={certs} />,
    );

    await user.click(screen.getByLabelText("SSL certificate"));
    await user.click(await screen.findByRole("option", { name: /api-cert/ }));

    expect(onValueChange).toHaveBeenCalledWith("9");
  });
});
