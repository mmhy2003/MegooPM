import { describe, expect, it } from "vitest";

import { EXPIRY_WARNING_DAYS, expiryInfo, formatDate } from "@/components/certificates/lib";

const NOW = new Date("2026-08-26T12:00:00Z");

function daysFromNow(days: number): string {
  return new Date(NOW.getTime() + days * 24 * 60 * 60 * 1000).toISOString();
}

describe("expiryInfo", () => {
  it("returns 'none' for a certificate without an expiry (pending)", () => {
    expect(expiryInfo(null, NOW)).toEqual({ level: "none", daysUntil: null, label: "—" });
    expect(expiryInfo(undefined, NOW).level).toBe("none");
  });

  it("returns 'none' for an unparseable date", () => {
    expect(expiryInfo("not-a-date", NOW).level).toBe("none");
  });

  it("flags a certificate comfortably in the future as ok", () => {
    const info = expiryInfo(daysFromNow(90), NOW);
    expect(info.level).toBe("ok");
    expect(info.daysUntil).toBe(90);
    expect(info.label).toBe("in 90 days");
  });

  it("flags a certificate within the warning window", () => {
    const info = expiryInfo(daysFromNow(10), NOW);
    expect(info.level).toBe("warning");
    expect(info.label).toBe("in 10 days");
  });

  it("treats the warning boundary itself as a warning", () => {
    expect(expiryInfo(daysFromNow(EXPIRY_WARNING_DAYS), NOW).level).toBe("warning");
    expect(expiryInfo(daysFromNow(EXPIRY_WARNING_DAYS + 1), NOW).level).toBe("ok");
  });

  it("labels an expiry later today distinctly", () => {
    const info = expiryInfo(daysFromNow(0.4), NOW);
    expect(info.level).toBe("warning");
    expect(info.daysUntil).toBe(0);
    expect(info.label).toBe("Expires today");
  });

  it("singularizes a one-day label", () => {
    expect(expiryInfo(daysFromNow(1.2), NOW).label).toBe("in 1 day");
  });

  it("marks a past certificate as expired", () => {
    const info = expiryInfo(daysFromNow(-3), NOW);
    expect(info.level).toBe("expired");
    expect(info.label).toBe("Expired");
    expect(info.daysUntil).toBeLessThan(0);
  });
});

describe("formatDate", () => {
  it("renders an ISO timestamp as YYYY-MM-DD", () => {
    expect(formatDate("2026-11-01T08:30:00Z")).toBe("2026-11-01");
  });

  it("returns a dash for missing or invalid input", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDate("nope")).toBe("—");
  });
});

import { challengeLabel, letsEncryptPayload } from "@/components/certificates/lib";

describe("challengeLabel", () => {
  it("describes how a certificate is validated", () => {
    expect(
      challengeLabel({ provider: "letsencrypt", challenge: "http-01", dns_provider_label: null }),
    ).toBe("HTTP-01");
    expect(
      challengeLabel({
        provider: "letsencrypt",
        challenge: "dns-01",
        dns_provider_label: "Cloudflare",
      }),
    ).toBe("DNS-01 · Cloudflare");
    expect(challengeLabel({ provider: "custom", challenge: null, dns_provider_label: null })).toBe(
      "—",
    );
  });
});

describe("letsEncryptPayload", () => {
  const base = {
    name: "wild",
    domains: ["*.example.com", "example.com"],
    accountEmail: "",
    dnsCredentialId: "",
  };

  it("requires saved credentials for DNS-01", () => {
    expect(letsEncryptPayload({ ...base, challenge: "dns-01" })).toEqual({
      ok: false,
      error: "Choose DNS provider credentials for DNS-01.",
    });
  });

  it("sends the credential id for DNS-01 and null for HTTP-01", () => {
    expect(letsEncryptPayload({ ...base, challenge: "dns-01", dnsCredentialId: "7" })).toEqual({
      ok: true,
      body: {
        name: "wild",
        domain_names: ["*.example.com", "example.com"],
        challenge: "dns-01",
        account_email: null,
        dns_credential_id: 7,
      },
    });
    expect(letsEncryptPayload({ ...base, challenge: "http-01", dnsCredentialId: "7" })).toMatchObject(
      { ok: true, body: { challenge: "http-01", dns_credential_id: null } },
    );
  });

  it("still validates name and domains", () => {
    expect(letsEncryptPayload({ ...base, name: " ", challenge: "http-01" })).toEqual({
      ok: false,
      error: "Give the certificate a name.",
    });
    expect(letsEncryptPayload({ ...base, domains: [], challenge: "http-01" })).toEqual({
      ok: false,
      error: "Enter at least one domain name.",
    });
  });
});
