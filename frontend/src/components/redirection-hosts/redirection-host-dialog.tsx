"use client";

import { useState } from "react";
import { toast } from "sonner";

import {
  REDIRECT_CODE_LABELS,
  REDIRECT_HTTP_CODES,
  REDIRECT_SCHEMES,
  redirectionHosts,
  type Certificate,
  type RedirectScheme,
  type RedirectionHost,
} from "@/lib/api";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

const SCHEME_LABELS: Record<RedirectScheme, string> = {
  auto: "auto (keep request scheme)",
  http: "http",
  https: "https",
};

/** Boolean feature toggles rendered as a switch grid. */
const TOGGLES = [
  ["ssl_forced", "Force SSL", "Redirect :80 to HTTPS"],
  ["http2_support", "HTTP/2", "Enable HTTP/2 on the TLS listener"],
  ["hsts_enabled", "HSTS", "Emit a Strict-Transport-Security header"],
  ["hsts_subdomains", "HSTS subdomains", "Include subdomains in HSTS"],
  ["block_exploits", "Block exploits", "Block common exploit probes"],
] as const;

type ToggleKey = (typeof TOGGLES)[number][0];

interface FormState {
  domains: string[];
  forwardDomainName: string;
  forwardScheme: RedirectScheme;
  forwardHttpCode: number;
  preservePath: boolean;
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
    block_exploits: false,
  };
}

function stateFromHost(host: RedirectionHost | null | undefined): FormState {
  if (!host) {
    return {
      domains: [],
      forwardDomainName: "",
      forwardScheme: "auto",
      forwardHttpCode: 302,
      preservePath: true,
      certificateId: valueFromCertificateId(null),
      enabled: true,
      toggles: emptyToggles(),
    };
  }
  return {
    domains: [...host.domain_names],
    forwardDomainName: host.forward_domain_name,
    forwardScheme: host.forward_scheme,
    forwardHttpCode: host.forward_http_code,
    preservePath: host.preserve_path,
    certificateId: valueFromCertificateId(host.certificate_id),
    enabled: host.enabled,
    toggles: {
      ssl_forced: host.ssl_forced,
      http2_support: host.http2_support,
      hsts_enabled: host.hsts_enabled,
      hsts_subdomains: host.hsts_subdomains,
      block_exploits: host.block_exploits,
    },
  };
}

export function RedirectionHostDialog({
  open,
  onOpenChange,
  host,
  certificates,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  host?: RedirectionHost | null;
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
    if (!form.forwardDomainName.trim()) {
      setError("Enter a forward domain to redirect to.");
      return;
    }

    const payload = {
      domain_names: domains,
      forward_domain_name: form.forwardDomainName.trim(),
      forward_scheme: form.forwardScheme,
      forward_http_code: form.forwardHttpCode,
      preserve_path: form.preservePath,
      certificate_id: certificateIdFromValue(form.certificateId),
      enabled: form.enabled,
      // Not editable here; preserve any operator-authored value on edit.
      advanced_config: host?.advanced_config ?? "",
      ...form.toggles,
    };

    setSaving(true);
    try {
      if (isEdit && host) {
        await redirectionHosts.update(host.id, payload);
      } else {
        await redirectionHosts.create(payload);
      }
      toast.success(isEdit ? "Redirection host updated" : "Redirection host created");
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
          <DialogTitle>
            {isEdit ? "Edit redirection host" : "New redirection host"}
          </DialogTitle>
          <DialogDescription>
            Answer for a set of domains and redirect them to another domain.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="redir-domains">Domain names</Label>
            <DomainTagsInput
              id="redir-domains"
              value={form.domains}
              onChange={(domains) => setForm((p) => ({ ...p, domains }))}
              onPendingInvalidChange={setDomainsInvalid}
              placeholder="old.example.com"
              disabled={saving}
            />
            <p className="text-xs text-muted-foreground">
              Press Enter or comma after each domain. Wildcards like <code>*.example.com</code>{" "}
              are allowed.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="redir-forward-domain">Forward domain</Label>
            <Input
              id="redir-forward-domain"
              value={form.forwardDomainName}
              onChange={(e) => setForm((p) => ({ ...p, forwardDomainName: e.target.value }))}
              placeholder="new.example.com"
              disabled={saving}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="redir-scheme">Forward scheme</Label>
            <Select
              value={form.forwardScheme}
              onValueChange={(value) =>
                setForm((p) => ({ ...p, forwardScheme: value as RedirectScheme }))
              }
            >
              <SelectTrigger id="redir-scheme" disabled={saving}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {REDIRECT_SCHEMES.map((scheme) => (
                  <SelectItem key={scheme} value={scheme}>
                    {SCHEME_LABELS[scheme]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="redir-code">HTTP status code</Label>
            <Select
              value={String(form.forwardHttpCode)}
              onValueChange={(value) =>
                setForm((p) => ({ ...p, forwardHttpCode: Number.parseInt(value as string, 10) }))
              }
            >
              <SelectTrigger id="redir-code" disabled={saving}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {REDIRECT_HTTP_CODES.map((code) => (
                  <SelectItem key={code} value={String(code)}>
                    {REDIRECT_CODE_LABELS[code] ?? String(code)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="sm:col-span-2">
            <CertificateSelect
              id="redir-certificate"
              value={form.certificateId}
              onValueChange={(v) => setForm((p) => ({ ...p, certificateId: v }))}
              certificates={certificates}
              disabled={saving}
              hint="Optional — terminate TLS for the redirecting domains."
            />
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="flex items-start gap-2 sm:col-span-2">
            <Switch
              checked={form.preservePath}
              onCheckedChange={(v) => setForm((p) => ({ ...p, preservePath: v }))}
              disabled={saving}
            />
            <span className="space-y-0.5">
              <span className="block text-sm font-medium leading-none">Preserve path</span>
              <span className="block text-xs text-muted-foreground">
                Append the original request URI to the target
              </span>
            </span>
          </label>
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
