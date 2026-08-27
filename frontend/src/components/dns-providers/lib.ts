/**
 * Pure helpers for the DNS providers UI (React-free, unit-tested).
 */
import type { DnsCredential, DnsProviderField } from "@/lib/api";

export function credentialLabel(cred: Pick<DnsCredential, "name" | "provider_label">): string {
  return `${cred.name} · ${cred.provider_label}`;
}

/** "auth_token" -> "Auth token" (mirrors the backend's humanize()). */
export function fieldLabel(name: string): string {
  const spaced = name.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function emptyValues(fields: Pick<DnsProviderField, "name">[]): Record<string, string> {
  return Object.fromEntries(fields.map((f) => [f.name, ""]));
}

/**
 * Trim every value and drop blanks. The backend treats an omitted secret on
 * update as "keep the stored value", so blank secrets are simply not sent.
 */
export function buildOptionsPayload(
  fields: DnsProviderField[],
  values: Record<string, string>,
): Record<string, string> {
  const payload: Record<string, string> = {};
  for (const field of fields) {
    const value = (values[field.name] ?? "").trim();
    if (value.length > 0) payload[field.name] = value;
  }
  return payload;
}

/** True when no secret field has a value — a new credential cannot be saved like that. */
export function missingSecret(fields: DnsProviderField[], values: Record<string, string>): boolean {
  return !fields.some((f) => f.secret && (values[f.name] ?? "").trim().length > 0);
}
