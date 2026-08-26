"use client";

import { useState } from "react";
import { toast } from "sonner";

import {
  HTTP_SCHEMES,
  proxyHosts,
  type AccessList,
  type HttpScheme,
  type ProxyHost,
  type Upstream,
} from "@/lib/api";
import {
  describeError,
  formatDomains,
  parseDomains,
} from "@/components/proxy-hosts/lib";
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
import { Textarea } from "@/components/ui/textarea";

const SCHEME_LABELS: Record<HttpScheme, string> = { http: "http", https: "https" };

/** Boolean feature toggles rendered as a switch grid. */
const TOGGLES = [
  ["ssl_forced", "Force SSL", "Redirect :80 to HTTPS"],
  ["http2_support", "HTTP/2", "Enable HTTP/2 on the TLS listener"],
  ["hsts_enabled", "HSTS", "Emit a Strict-Transport-Security header"],
  ["hsts_subdomains", "HSTS subdomains", "Include subdomains in HSTS"],
  ["caching_enabled", "Cache assets", "Cache static assets"],
  ["block_exploits", "Block exploits", "Block common exploit probes"],
  ["allow_websocket_upgrade", "Websockets", "Pass Upgrade/Connection headers"],
] as const;

type ToggleKey = (typeof TOGGLES)[number][0];

/** Sentinel Select value for "no access list attached" (`null` on the wire). */
const NO_ACCESS_LIST = "none";

interface FormState {
  domains: string;
  upstreamId: string;
  accessListId: string;
  forwardScheme: HttpScheme;
  enabled: boolean;
  toggles: Record<ToggleKey, boolean>;
  advancedConfig: string;
}

function emptyToggles(): Record<ToggleKey, boolean> {
  return {
    ssl_forced: false,
    http2_support: false,
    hsts_enabled: false,
    hsts_subdomains: false,
    caching_enabled: false,
    block_exploits: false,
    allow_websocket_upgrade: false,
  };
}

function stateFromHost(host: ProxyHost | null | undefined): FormState {
  if (!host) {
    return {
      domains: "",
      upstreamId: "",
      accessListId: NO_ACCESS_LIST,
      forwardScheme: "http",
      enabled: true,
      toggles: emptyToggles(),
      advancedConfig: "",
    };
  }
  return {
    domains: formatDomains(host.domain_names),
    upstreamId: String(host.upstream_id),
    accessListId: host.access_list_id ? String(host.access_list_id) : NO_ACCESS_LIST,
    forwardScheme: host.forward_scheme,
    enabled: host.enabled ?? true,
    toggles: {
      ssl_forced: host.ssl_forced ?? false,
      http2_support: host.http2_support ?? false,
      hsts_enabled: host.hsts_enabled ?? false,
      hsts_subdomains: host.hsts_subdomains ?? false,
      caching_enabled: host.caching_enabled ?? false,
      block_exploits: host.block_exploits ?? false,
      allow_websocket_upgrade: host.allow_websocket_upgrade ?? false,
    },
    advancedConfig: host.advanced_config ?? "",
  };
}

export function ProxyHostDialog({
  open,
  onOpenChange,
  host,
  pools,
  lists,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  host?: ProxyHost | null;
  pools: Upstream[];
  lists: AccessList[];
  onSaved: () => void;
}) {
  const isEdit = Boolean(host);
  // Seeded from props on mount; the parent remounts this dialog (keyed) per
  // target, so no reset-on-open effect is needed.
  const [form, setForm] = useState<FormState>(() => stateFromHost(host));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function setToggle(key: ToggleKey, value: boolean) {
    setForm((prev) => ({ ...prev, toggles: { ...prev.toggles, [key]: value } }));
  }

  async function handleSubmit() {
    setError(null);
    const domains = parseDomains(form.domains);
    if (domains.length === 0) {
      setError("Enter at least one domain name.");
      return;
    }
    if (!form.upstreamId) {
      setError("Select an upstream pool to forward to.");
      return;
    }

    const payload = {
      domain_names: domains,
      upstream_id: Number.parseInt(form.upstreamId, 10),
      access_list_id:
        form.accessListId === NO_ACCESS_LIST
          ? null
          : Number.parseInt(form.accessListId, 10),
      forward_scheme: form.forwardScheme,
      enabled: form.enabled,
      advanced_config: form.advancedConfig,
      ...form.toggles,
      // CrowdSec enforcement is owned by the Security UI (MEG-22); pass the
      // existing values through untouched so this form never clobbers them.
      crowdsec_enabled: host?.crowdsec_enabled ?? false,
      crowdsec_appsec_enabled: host?.crowdsec_appsec_enabled ?? false,
    };

    setSaving(true);
    try {
      if (isEdit && host) {
        await proxyHosts.update(host.id, payload);
      } else {
        await proxyHosts.create(payload);
      }
      toast.success(isEdit ? "Proxy host updated" : "Proxy host created");
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

  const noPools = pools.length === 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit proxy host" : "New proxy host"}</DialogTitle>
          <DialogDescription>
            Terminate domain names and forward matching traffic to an upstream pool.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="host-domains">Domain names</Label>
            <Input
              id="host-domains"
              value={form.domains}
              onChange={(e) => setForm((p) => ({ ...p, domains: e.target.value }))}
              placeholder="example.com, www.example.com"
              disabled={saving}
            />
            <p className="text-xs text-muted-foreground">
              Comma- or space-separated. Wildcards like <code>*.example.com</code> are allowed.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="host-upstream">Upstream pool</Label>
            <Select
              value={form.upstreamId}
              onValueChange={(value) => setForm((p) => ({ ...p, upstreamId: value as string }))}
            >
              <SelectTrigger id="host-upstream" disabled={saving || noPools}>
                <SelectValue placeholder={noPools ? "No pools — create one first" : "Select a pool"} />
              </SelectTrigger>
              <SelectContent>
                {pools.map((pool) => (
                  <SelectItem key={pool.id} value={String(pool.id)}>
                    {pool.name} ({pool.backends?.length ?? 0} backends)
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="host-scheme">Forward scheme</Label>
            <Select
              value={form.forwardScheme}
              onValueChange={(value) =>
                setForm((p) => ({ ...p, forwardScheme: value as HttpScheme }))
              }
              items={SCHEME_LABELS}
            >
              <SelectTrigger id="host-scheme" disabled={saving}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {HTTP_SCHEMES.map((scheme) => (
                  <SelectItem key={scheme} value={scheme}>
                    {SCHEME_LABELS[scheme]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="host-access-list">Access list</Label>
            <Select
              value={form.accessListId}
              onValueChange={(value) =>
                setForm((p) => ({ ...p, accessListId: value as string }))
              }
            >
              <SelectTrigger id="host-access-list" disabled={saving}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_ACCESS_LIST}>None (public)</SelectItem>
                {lists.map((list) => (
                  <SelectItem key={list.id} value={String(list.id)}>
                    {list.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Gate this host behind basic-auth users and/or IP allow/deny rules.
            </p>
          </div>
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

        <div className="space-y-1.5">
          <Label htmlFor="host-advanced">Advanced nginx config</Label>
          <Textarea
            id="host-advanced"
            value={form.advancedConfig}
            onChange={(e) => setForm((p) => ({ ...p, advancedConfig: e.target.value }))}
            placeholder="# Raw directives injected into the server block"
            className="font-mono text-xs"
            disabled={saving}
          />
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
          <Button onClick={handleSubmit} disabled={saving || noPools}>
            {saving ? "Saving…" : isEdit ? "Save changes" : "Create host"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
