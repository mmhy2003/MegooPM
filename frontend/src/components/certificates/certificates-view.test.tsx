import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { certificates, dnsCredentials, dnsProviders, type Certificate } from "@/lib/api";
import { CertificatesView } from "@/components/certificates/certificates-view";

// vi.mock factories are hoisted above the imports, so a factory closing
// over a plain const fails at collection. vi.hoisted is the way in.
const useAuth = vi.hoisted(() => vi.fn(() => ({ user: { role: "admin" } })));
vi.mock("@/lib/auth/context", () => ({ useAuth }));

const useIsMobile = vi.hoisted(() => vi.fn(() => false));
vi.mock("@/hooks/use-mobile", () => ({ useIsMobile }));

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

describe("CertificatesView DNS providers tab", () => {
  afterEach(() => {
    useAuth.mockReturnValue({ user: { role: "admin" } });
  });

  it("is hidden from a member", async () => {
    // The endpoints behind it are admin-only, so the tab could only ever
    // show a member an error.
    useAuth.mockReturnValue({ user: { role: "member" } });
    await renderView([]);

    expect(screen.queryByRole("tab", { name: /DNS providers/i })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Certificates/i })).toBeInTheDocument();
  });

  it("is there for an admin", async () => {
    await renderView([]);
    expect(screen.getByRole("tab", { name: /DNS providers/i })).toBeInTheDocument();
  });
});

describe("CertificatesView on a phone", () => {
  afterEach(() => {
    useIsMobile.mockReturnValue(false);
  });

  it("lays each certificate out as a card, with its actions", async () => {
    useIsMobile.mockReturnValue(true);
    await renderView([makeCert()]);

    expect(screen.getByText("wildcard")).toBeInTheDocument();
    expect(screen.getByText("*.example.com")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete wildcard" })).toBeInTheDocument();
  });
});
