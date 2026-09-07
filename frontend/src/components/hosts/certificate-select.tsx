"use client";

import type { Certificate } from "@/lib/api";
import { Label } from "@/components/ui/label";
import { SearchableSelect } from "@/components/ui/searchable-select";

/** Sentinel Select value for "no certificate" (`null` on the wire). */
export const NO_CERTIFICATE = "none";

/** Map a form Select value back to a nullable `certificate_id`. */
export function certificateIdFromValue(value: string): number | null {
  return value === NO_CERTIFICATE ? null : Number.parseInt(value, 10);
}

/** Map a nullable `certificate_id` to the Select value. */
export function valueFromCertificateId(id: number | null | undefined): string {
  return id != null ? String(id) : NO_CERTIFICATE;
}

/** One label, used for both the option and the trigger, so they cannot drift. */
function certificateLabel(cert: Certificate): string {
  return cert.domain_names[0] ? `${cert.name} (${cert.domain_names[0]})` : cert.name;
}

/**
 * Shared TLS-certificate picker for the redirection / dead / stream dialogs.
 *
 * The "None" option maps to a plain (HTTP / non-TLS) listener; concrete certs
 * are labelled by name and primary domain so operators can tell them apart.
 */
export function CertificateSelect({
  id,
  value,
  onValueChange,
  certificates,
  disabled,
  noneLabel = "None (HTTP only)",
  hint,
}: {
  id: string;
  value: string;
  onValueChange: (value: string) => void;
  certificates: Certificate[];
  disabled?: boolean;
  noneLabel?: string;
  hint?: string;
}) {
  // value -> label, so the trigger shows a name and not a bare id — and so a
  // typed fragment matches the name or the primary domain, which is how an
  // operator with forty certificates actually remembers one.
  const items: Record<string, string> = { [NO_CERTIFICATE]: noneLabel };
  for (const cert of certificates) items[String(cert.id)] = certificateLabel(cert);

  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>SSL certificate</Label>
      <SearchableSelect
        id={id}
        value={value}
        items={items}
        onValueChange={onValueChange}
        disabled={disabled}
        searchLabel="Search certificates"
      />
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}
