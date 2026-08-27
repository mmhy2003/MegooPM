/**
 * Pure helpers for the Certificates UI.
 *
 * Kept React-free so the expiry classification — the piece worth pinning down,
 * since it drives the near-expiry warnings in the AC — can be unit-tested in
 * isolation. Error/domain normalization is reused from the Proxy Hosts lib.
 */

import type { AcmeChallenge, Certificate, LetsEncryptCertificateCreate } from "@/lib/api";

/** How close a certificate is to (or past) its expiry, for visual flagging. */
export type ExpiryLevel = "none" | "ok" | "warning" | "expired";

export interface ExpiryInfo {
  level: ExpiryLevel;
  /** Whole days until expiry; negative once expired, `null` when unknown. */
  daysUntil: number | null;
  /** Short human label, e.g. "in 12 days", "Expired", "Expires today". */
  label: string;
}

/** Certs within this many days of expiry are flagged as a warning. */
export const EXPIRY_WARNING_DAYS = 30;

const MS_PER_DAY = 24 * 60 * 60 * 1000;

/**
 * Classify a certificate's `expires_on` into a level + label.
 *
 * `now` is injectable so the classification is deterministic under test. A
 * pending certificate (no material yet) has no expiry and returns `"none"`.
 */
export function expiryInfo(
  expiresOn: string | null | undefined,
  now: Date = new Date(),
): ExpiryInfo {
  if (!expiresOn) {
    return { level: "none", daysUntil: null, label: "—" };
  }
  const expiry = new Date(expiresOn);
  if (Number.isNaN(expiry.getTime())) {
    return { level: "none", daysUntil: null, label: "—" };
  }

  // Round toward the day boundary so "expires later today" reads as 0, not -0.
  const daysUntil = Math.floor((expiry.getTime() - now.getTime()) / MS_PER_DAY);

  if (daysUntil < 0) {
    return { level: "expired", daysUntil, label: "Expired" };
  }
  if (daysUntil === 0) {
    return { level: "warning", daysUntil, label: "Expires today" };
  }
  const label = `in ${daysUntil} day${daysUntil === 1 ? "" : "s"}`;
  if (daysUntil <= EXPIRY_WARNING_DAYS) {
    return { level: "warning", daysUntil, label };
  }
  return { level: "ok", daysUntil, label };
}

/** Format an ISO date as a short, locale-stable `YYYY-MM-DD` for tables. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toISOString().slice(0, 10);
}

/** "HTTP-01", "DNS-01 · Cloudflare", or "—" for non-ACME certificates. */
export function challengeLabel(
  cert: Pick<Certificate, "provider" | "challenge" | "dns_provider_label">,
): string {
  if (cert.provider !== "letsencrypt" || !cert.challenge) return "—";
  if (cert.challenge === "dns-01") {
    return cert.dns_provider_label ? `DNS-01 · ${cert.dns_provider_label}` : "DNS-01";
  }
  return "HTTP-01";
}

export interface LetsEncryptFormInput {
  name: string;
  /** Committed domain tags (already normalised and validated by the input). */
  domains: string[];
  challenge: AcmeChallenge;
  accountEmail: string;
  /** Selected saved-credential id as a string (Select value); "" = none. */
  dnsCredentialId: string;
}

export type LetsEncryptFormResult =
  | { ok: true; body: LetsEncryptCertificateCreate }
  | { ok: false; error: string };

/** Validate the Let's Encrypt form and build the request body. */
export function letsEncryptPayload(input: LetsEncryptFormInput): LetsEncryptFormResult {
  const name = input.name.trim();
  const domains = input.domains;
  if (!name) return { ok: false, error: "Give the certificate a name." };
  if (domains.length === 0) return { ok: false, error: "Enter at least one domain name." };
  const isDns = input.challenge === "dns-01";
  if (isDns && !input.dnsCredentialId) {
    return { ok: false, error: "Choose DNS provider credentials for DNS-01." };
  }
  return {
    ok: true,
    body: {
      name,
      domain_names: domains,
      challenge: input.challenge,
      account_email: input.accountEmail.trim() || null,
      dns_credential_id: isDns ? Number(input.dnsCredentialId) : null,
    },
  };
}
