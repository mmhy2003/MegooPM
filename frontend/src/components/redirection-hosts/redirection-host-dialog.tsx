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
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";

const SCHEME_LABELS: Record<RedirectScheme, string> = {
  auto: "auto (keep request scheme)",
  http: "http",
  https: "https",
};

type ToggleDef = readonly [ToggleKey, string, string];

/** TLS options — meaningless without a certificate, so the SSL tab gates them. */
const TLS_TOGGLES: readonly ToggleDef[] = [
  ["ssl_forced", "Force SSL", "Redirect :80 to HTTPS"],
  ["http2_support", "HTTP/2", "Enable HTTP/2 on the TLS listener"],
  ["hsts_enabled", "HSTS", "Emit a Strict-Transport-Security header"],
  ["hsts_subdomains", "HSTS subdomains", "Include subdomains in HSTS"],
];

/** Security options that apply with or without TLS — these stay on Details. */
const DETAILS_TOGGLES: readonly ToggleDef[] = [
  ["block_exploits", "Block exploits", "Block common exploit probes"],
];

type ToggleKey =
  | "ssl_forced"
  | "http2_support"
  | "hsts_enabled"
  | "hsts_subdomains"
  | "block_exploits";

type DialogTab = "details" | "ssl";

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

/** One switch with its label and hint — shared by both tabs. */
function ToggleRow({
  label,
  hint,
  checked,
  onCheckedChange,
  disabled,
  className,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onCheckedChange: (value: boolean) => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <label className={`flex items-start gap-2 ${className ?? ""}`}>
      {/* The wrapping label also contains the hint, so name the switch explicitly
          — same as the proxy-host dialog's ToggleGrid. */}
      <Switch
        aria-label={label}
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
      />
      <span className="space-y-0.5">
        <span className="block text-sm font-medium leading-none">{label}</span>
        <span className="block text-xs text-muted-foreground">{hint}</span>
      </span>
    </label>
  );
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
  // Seeded from props on mount; the parent remounts this dialog (keyed) per
  // target, so neither the form nor the tab needs a reset-on-open effect.
  const [form, setForm] = useState<FormState>(() => stateFromHost(host));
  const [tab, setTab] = useState<DialogTab>("details");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [domainsInvalid, setDomainsInvalid] = useState(false);

  // Force SSL / HTTP2 / HSTS all describe a TLS listener this host will not have.
  const noCertificate = certificateIdFromValue(form.certificateId) === null;

  function setToggle(key: ToggleKey, value: boolean) {
    setForm((prev) => ({ ...prev, toggles: { ...prev.toggles, [key]: value } }));
  }

  /** Report a problem and reveal the field it refers to. */
  function fail(message: string, on: DialogTab = "details") {
    setTab(on);
    setError(message);
  }

  async function handleSubmit() {
    setError(null);
    // Every check below concerns a Details field, so surface that tab with the
    // error — otherwise the operator reads a complaint about a hidden input.
    if (domainsInvalid) {
      fail("Fix the highlighted domain first.");
      return;
    }
    const domains = form.domains;
    if (domains.length === 0) {
      fail("Enter at least one domain name.");
      return;
    }
    if (!form.forwardDomainName.trim()) {
      fail("Enter a forward domain to redirect to.");
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

        {/* Domains identify the host, so they stay visible above the tabs. */}
        <div className="space-y-1.5">
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

        <Tabs value={tab} onValueChange={(value) => setTab(value as DialogTab)}>
          <TabsList>
            <TabsTab value="details">Details</TabsTab>
            <TabsTab value="ssl">SSL</TabsTab>
          </TabsList>

          <TabsPanel value="details" className="space-y-4 pt-2">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="redir-forward-domain">Forward domain</Label>
                <Input
                  id="redir-forward-domain"
                  value={form.forwardDomainName}
                  onChange={(e) =>
                    setForm((p) => ({ ...p, forwardDomainName: e.target.value }))
                  }
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
                    setForm((p) => ({
                      ...p,
                      forwardHttpCode: Number.parseInt(value as string, 10),
                    }))
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
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <ToggleRow
                className="sm:col-span-2"
                label="Preserve path"
                hint="Append the original request URI to the target"
                checked={form.preservePath}
                onCheckedChange={(v) => setForm((p) => ({ ...p, preservePath: v }))}
                disabled={saving}
              />
              {DETAILS_TOGGLES.map(([key, label, hint]) => (
                <ToggleRow
                  key={key}
                  label={label}
                  hint={hint}
                  checked={form.toggles[key]}
                  onCheckedChange={(v) => setToggle(key, v)}
                  disabled={saving}
                />
              ))}
              <ToggleRow
                label="Enabled"
                hint="Disabled hosts are excluded from the nginx config"
                checked={form.enabled}
                onCheckedChange={(v) => setForm((p) => ({ ...p, enabled: v }))}
                disabled={saving}
              />
            </div>
          </TabsPanel>

          <TabsPanel value="ssl" className="space-y-4 pt-2">
            <CertificateSelect
              id="redir-certificate"
              value={form.certificateId}
              onValueChange={(v) => setForm((p) => ({ ...p, certificateId: v }))}
              certificates={certificates}
              disabled={saving}
              hint="Optional — terminate TLS for the redirecting domains. Without one the options below have no effect."
            />
            <div className="grid gap-3 sm:grid-cols-2">
              {TLS_TOGGLES.map(([key, label, hint]) => (
                <ToggleRow
                  key={key}
                  label={label}
                  hint={hint}
                  checked={form.toggles[key]}
                  onCheckedChange={(v) => setToggle(key, v)}
                  disabled={saving || noCertificate}
                />
              ))}
            </div>
          </TabsPanel>
        </Tabs>

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
