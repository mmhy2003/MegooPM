/**
 * Typed client for the certificate-management endpoints.
 *
 * Certificates are TLS material managed by MegooPM: issued via Let's Encrypt
 * (async ACME — the request returns a tracking task), uploaded as custom PEM, or
 * self-signed. Private key material is write-only: it is accepted on upload but
 * never returned. Shapes are derived from the generated OpenAPI schema.
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type Certificate = Schemas["CertificateRead"];
export type CustomCertificateCreate = Schemas["CustomCertificateCreate"];
export type LetsEncryptCertificateCreate = Schemas["LetsEncryptCertificateCreate"];
export type CertificateIssued = Schemas["CertificateIssued"];
export type CertificateProvider = Schemas["CertificateProvider"];
export type CertificateStatus = Schemas["CertificateStatus"];
export type AcmeChallenge = "http-01" | "dns-01";

const BASE = "/api/v1/certificates";

export const certificates = {
  list: () => api.get<Certificate[]>(BASE),
  get: (id: number) => api.get<Certificate>(`${BASE}/${id}`),
  /** Validate + store an uploaded PEM certificate (returns the stored cert). */
  uploadCustom: (body: CustomCertificateCreate) =>
    api.post<Certificate>(`${BASE}/custom`, body),
  /** Enqueue ACME issuance; the response carries a `task_id` to poll. */
  requestLetsEncrypt: (body: LetsEncryptCertificateCreate) =>
    api.post<CertificateIssued>(`${BASE}/letsencrypt`, body),
  /** Enqueue renewal/re-issuance; the response carries a `task_id` to poll. */
  renew: (id: number) => api.post<CertificateIssued>(`${BASE}/${id}/renew`),
  remove: (id: number) => api.delete<void>(`${BASE}/${id}`),
} as const;

export const ACME_CHALLENGES: readonly AcmeChallenge[] = ["http-01", "dns-01"] as const;

/** Human labels for the certificate provider enum. */
export const CERT_PROVIDER_LABELS: Record<CertificateProvider, string> = {
  letsencrypt: "Let's Encrypt",
  custom: "Custom",
  self_signed: "Self-signed",
};
