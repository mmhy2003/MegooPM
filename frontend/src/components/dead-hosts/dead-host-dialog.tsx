"use client";

import { useState } from "react";
import { toast } from "sonner";

import { deadHosts, type Certificate, type DeadHost } from "@/lib/api";
import {
  describeError,
} from "@/components/proxy-hosts/lib";
import {
  CertificateSelect,
  certificateIdFromValue,
  valueFromCertificateId,
} from "@/components/hosts/certificate-select";
import { DomainTagsInput } from "@/components/domains/domain-tags-input";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

/** Boolean feature toggles rendered as a switch grid. */
const TOGGLES = [
  ["ssl_forced", "Force SSL", "Redirect :80 to HTTPS"],
  ["http2_support", "HTTP/2", "Enable HTTP/2 on the TLS listener"],
  ["hsts_enabled", "HSTS", "Emit a Strict-Transport-Security header"],
  ["hsts_subdomains", "HSTS subdomains", "Include subdomains in HSTS"],
] as const;

type ToggleKey = (typeof TOGGLES)[number][0];

interface FormState {
  domains: string[];
  certificateId: string;
  enabled: boolean;
  toggles: Record<ToggleKey, boolean>;
}

function emptyToggles(): Record<ToggleKey, boolean> {
  return {
    ssl_forced: false,
    http2_support: false,
    hsts_enabled: false,
    hsts_subdomains: false,
  };
}

function stateFromHost(host: DeadHost | null | undefined): FormState {
  if (!host) {
    return {
      domains: [],
      certificateId: valueFromCertificateId(null),
      enabled: true,
      toggles: emptyToggles(),
    };
  }
  return {
    domains: [...host.domain_names],
    certificateId: valueFromCertificateId(host.certificate_id),
    enabled: host.enabled,
    toggles: {
      ssl_forced: host.ssl_forced,
      http2_support: host.http2_support,
      hsts_enabled: host.hsts_enabled,
      hsts_subdomains: host.hsts_subdomains,
    },
  };
}

export function DeadHostDialog({
  open,
  onOpenChange,
  host,
  certificates,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  host?: DeadHost | null;
  certificates: Certificate[];
  onSaved: () => void;
}) {
  const isEdit = Boolean(host);
  const [form, setForm] = useState<FormState>(() => stateFromHost(host));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [domainsInvalid, setDomainsInvalid] = useState(false);

  function setToggle(key: ToggleKey, value: boolean) {
    setForm((prev) => ({ ...prev, toggles: { ...prev.toggles, [key]: value } }));
  }

  async function handleSubmit() {
    setError(null);
    if (domainsInvalid) {
      setError("Fix the highlighted domain first.");
      return;
    }
    const domains = form.domains;
    if (domains.length === 0) {
      setError("Enter at least one domain name.");
      return;
    }

    const payload = {
      domain_names: domains,
      certificate_id: certificateIdFromValue(form.certificateId),
      enabled: form.enabled,
      // Not editable here; preserve any operator-authored value on edit.
      advanced_config: host?.advanced_config ?? "",
      ...form.toggles,
    };

    setSaving(true);
    try {
      if (isEdit && host) {
        await deadHosts.update(host.id, payload);
      } else {
        await deadHosts.create(payload);
      }
      toast.success(isEdit ? "404 host updated" : "404 host created");
      onOpenChange(false);
      onSaved();
    } catch (err) {
      const described = describeError(err);
      setError(described.message);
      toast.error(described.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit 404 host" : "New 404 host"}</DialogTitle>
          <DialogDescription>
            Answer for a set of domains and return a 404 for every request.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="dead-domains">Domain names</Label>
            <DomainTagsInput
              id="dead-domains"
              value={form.domains}
              onChange={(domains) => setForm((p) => ({ ...p, domains }))}
              onPendingInvalidChange={setDomainsInvalid}
              placeholder="parked.example.com"
              disabled={saving}
            />
            <p className="text-xs text-muted-foreground">
              Press Enter or comma after each domain. Wildcards like <code>*.example.com</code>{" "}
              are allowed.
            </p>
          </div>

          <CertificateSelect
            id="dead-certificate"
            value={form.certificateId}
            onValueChange={(v) => setForm((p) => ({ ...p, certificateId: v }))}
            certificates={certificates}
            disabled={saving}
            hint="Optional — terminate TLS for these domains."
          />
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          {TOGGLES.map(([key, label, hint]) => (
            <label key={key} className="flex items-start gap-2">
              <Switch
                checked={form.toggles[key]}
                onCheckedChange={(v) => setToggle(key, v)}
                disabled={saving}
              />
              <span className="space-y-0.5">
                <span className="block text-sm font-medium leading-none">{label}</span>
                <span className="block text-xs text-muted-foreground">{hint}</span>
              </span>
            </label>
          ))}
          <label className="flex items-start gap-2">
            <Switch
              checked={form.enabled}
              onCheckedChange={(v) => setForm((p) => ({ ...p, enabled: v }))}
              disabled={saving}
            />
            <span className="space-y-0.5">
              <span className="block text-sm font-medium leading-none">Enabled</span>
              <span className="block text-xs text-muted-foreground">
                Disabled hosts are excluded from the nginx config
              </span>
            </span>
          </label>
        </div>

        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={saving}>
            {saving ? "Saving…" : isEdit ? "Save changes" : "Create host"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
