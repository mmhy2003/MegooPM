import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { certificates, dnsCredentials, dnsProviders, type Certificate } from "@/lib/api";
import { CertificatesView } from "@/components/certificates/certificates-view";

function makeCert(over: Partial<Certificate> = {}): Certificate {
  return {
    id: 1,
    name: "wildcard",
    domain_names: ["*.example.com"],
    provider: "letsencrypt",
    challenge: "dns-01",
    dns_provider: null,
    status: "active",
    expires_on: "2027-01-01T00:00:00Z",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    ...over,
  } as Certificate;
}

async function renderView(rows: Certificate[]) {
  vi.spyOn(certificates, "list").mockResolvedValue(rows);
  // The DNS providers tab panel mounts alongside the certificates one and
  // fetches on mount — unmocked, those two requests reject into the console.
  vi.spyOn(dnsCredentials, "list").mockResolvedValue([]);
  vi.spyOn(dnsProviders, "catalog").mockResolvedValue([]);
  render(<CertificatesView />);
  await screen.findByRole("searchbox", { name: "Search certificates" });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("CertificatesView search", () => {
  it("matches a certificate by one of its domains", async () => {
    const user = userEvent.setup();
    await renderView([
      makeCert({ id: 1, name: "wildcard", domain_names: ["*.example.com"] }),
      makeCert({ id: 2, name: "internal", domain_names: ["blog.internal"] }),
    ]);

    await user.type(screen.getByRole("searchbox"), "blog.internal");

    expect(screen.getByText("internal")).toBeInTheDocument();
    expect(screen.queryByText("wildcard")).not.toBeInTheDocument();
  });

  it("distinguishes a filtered-empty table from an empty instance", async () => {
    const user = userEvent.setup();
    await renderView([]);
    expect(screen.getByText(/no certificates yet/i)).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox"), "nonesuch");

    expect(screen.getByText(/no certificates match/i)).toBeInTheDocument();
  });
});
