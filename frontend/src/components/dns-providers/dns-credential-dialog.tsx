"use client";

import { useState } from "react";
import { toast } from "sonner";

import { dnsCredentials, type DnsCredential, type DnsProviderInfo } from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { buildOptionsPayload, emptyValues, missingSecret } from "@/components/dns-providers/lib";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export function DnsCredentialDialog({
  open,
  onOpenChange,
  credential,
  catalog,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` = create mode. Remount with a `key` per target (ProxyHostDialog pattern). */
  credential: DnsCredential | null;
  catalog: DnsProviderInfo[];
  onSaved: () => void;
}) {
  const isEdit = credential !== null;
  const [name, setName] = useState(credential?.name ?? "");
  const [providerId, setProviderId] = useState(credential?.provider ?? catalog[0]?.id ?? "");
  const provider = catalog.find((p) => p.id === providerId) ?? null;
  const [values, setValues] = useState<Record<string, string>>(() => ({
    ...emptyValues(provider?.fields ?? []),
    ...(credential?.options ?? {}),
  }));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function changeProvider(id: string) {
    setProviderId(id);
    const next = catalog.find((p) => p.id === id);
    setValues(emptyValues(next?.fields ?? []));
  }

  async function submit() {
    setError(null);
    if (!name.trim()) return setError("Give the credentials a name.");
    if (!provider) return setError("Choose a DNS provider.");
    if (!isEdit && missingSecret(provider.fields, values)) {
      return setError("Enter at least one credential (secret) field.");
    }
    const options = buildOptionsPayload(provider.fields, values);
    setSaving(true);
    try {
      if (isEdit) {
        await dnsCredentials.update(credential.id, { name: name.trim(), options });
        toast.success("Credentials updated");
      } else {
        await dnsCredentials.create({ name: name.trim(), provider: provider.id, options });
        toast.success("Credentials saved");
      }
      onOpenChange(false);
      onSaved();
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit DNS credentials" : "New DNS credentials"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Leave a secret blank to keep its stored value."
              : "Saved once, encrypted at rest, reused by every DNS-01 certificate and renewal."}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="dns-name">Name</Label>
            <Input
              id="dns-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Cloudflare — prod token"
              disabled={saving}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="dns-provider">Provider</Label>
            <Select
              value={providerId}
              onValueChange={(v) => changeProvider(v as string)}
              items={Object.fromEntries(catalog.map((p) => [p.id, p.label]))}
            >
              <SelectTrigger id="dns-provider" disabled={saving || isEdit}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {catalog.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {provider?.description ? (
              <p className="text-xs text-muted-foreground">{provider.description}</p>
            ) : null}
            {isEdit ? (
              <p className="text-xs text-muted-foreground">
                The provider cannot change; create new credentials for a different provider.
              </p>
            ) : null}
          </div>

          {provider?.fields.map((field) => (
            <div key={field.name} className="space-y-1.5">
              <Label htmlFor={`dns-field-${field.name}`}>{field.label}</Label>
              <Input
                id={`dns-field-${field.name}`}
                type={field.secret ? "password" : "text"}
                autoComplete={field.secret ? "new-password" : "off"}
                value={values[field.name] ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [field.name]: e.target.value }))}
                placeholder={
                  field.secret && isEdit && credential.secret_fields.includes(field.name)
                    ? "unchanged — leave blank to keep"
                    : undefined
                }
                disabled={saving}
              />
              {field.help ? <p className="text-xs text-muted-foreground">{field.help}</p> : null}
            </div>
          ))}

          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={saving}>
            {saving ? "Saving…" : isEdit ? "Save changes" : "Save credentials"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
