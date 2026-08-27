/**
 * Typed client for the DNS-01 provider catalog and saved DNS credentials.
 *
 * The catalog is generated server-side from dns-lexicon (provider id, label,
 * and the credential fields each provider takes). Credentials are saved once,
 * encrypted at rest, and referenced by certificates; reads expose only the
 * *names* of secret fields, never their values.
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type DnsProviderInfo = Schemas["DnsProviderInfoRead"];
export type DnsProviderField = Schemas["DnsProviderFieldRead"];
export type DnsCredential = Schemas["DnsCredentialRead"];
export type DnsCredentialCreate = Schemas["DnsCredentialCreate"];
export type DnsCredentialUpdate = Schemas["DnsCredentialUpdate"];
export type DnsCredentialVerify = Schemas["DnsCredentialVerify"];
export type DnsCredentialVerified = Schemas["DnsCredentialVerified"];

const PROVIDERS = "/api/v1/dns-providers";
const CREDENTIALS = "/api/v1/dns-credentials";

export const dnsProviders = {
  catalog: () => api.get<DnsProviderInfo[]>(PROVIDERS),
} as const;

export const dnsCredentials = {
  list: () => api.get<DnsCredential[]>(CREDENTIALS),
  create: (body: DnsCredentialCreate) => api.post<DnsCredential>(CREDENTIALS, body),
  update: (id: number, body: DnsCredentialUpdate) =>
    api.patch<DnsCredential>(`${CREDENTIALS}/${id}`, body),
  /** Writes and removes a probe TXT record with the real provider (400 on failure). */
  verify: (id: number, body: DnsCredentialVerify) =>
    api.post<DnsCredentialVerified>(`${CREDENTIALS}/${id}/verify`, body),
  /** 409 while certificates still reference the credential. */
  remove: (id: number) => api.delete<void>(`${CREDENTIALS}/${id}`),
} as const;
