"use client";

import type { Certificate } from "@/lib/api";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

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
  // base-ui renders the raw value in the trigger unless the root is given
  // `items` to map value -> label. Without it a picked certificate shows as its
  // bare id, which tells an operator nothing.
  const items: Record<string, string> = { [NO_CERTIFICATE]: noneLabel };
  for (const cert of certificates) items[String(cert.id)] = certificateLabel(cert);

  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>SSL certificate</Label>
      <Select value={value} onValueChange={(v) => onValueChange(v as string)} items={items}>
        <SelectTrigger id={id} disabled={disabled}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={NO_CERTIFICATE}>{noneLabel}</SelectItem>
          {certificates.map((cert) => (
            <SelectItem key={cert.id} value={String(cert.id)}>
              {certificateLabel(cert)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}
