"use client";

import { useState } from "react";
import { toast } from "sonner";

import {
  type CustomPageSummary,
  proxyHosts,
  type AccessList,
  type Certificate,
  type ProxyHost,
  type Upstream,
} from "@/lib/api";
import {
  NO_ACCESS_LIST,
  NO_CERTIFICATE,
  buildPayload,
  describeError,
  stateFromHost,
  validateForm,
  type DialogTab,
  type ProxyHostFormState,
  type ToggleKey,
} from "@/components/proxy-hosts/lib";
import { LocationsEditor } from "@/components/proxy-hosts/locations-editor";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

type ToggleDef = readonly [ToggleKey, string, string];

/** One label for a certificate, so option and trigger cannot disagree. */
function certLabel(cert: Certificate): string {
  return cert.status !== "active" ? `${cert.name} — ${cert.status}` : cert.name;
}

const FORWARDING_TOGGLES: readonly ToggleDef[] = [
  ["caching_enabled", "Cache assets", "Cache static assets"],
  ["block_exploits", "Block exploits", "Block common exploit probes"],
  ["allow_websocket_upgrade", "Websockets", "Pass Upgrade/Connection headers"],
];

const TLS_TOGGLES: readonly ToggleDef[] = [
  ["ssl_forced", "Force SSL", "Redirect :80 to HTTPS"],
  ["http2_support", "HTTP/2", "Enable HTTP/2 on the TLS listener"],
  ["hsts_enabled", "HSTS", "Emit a Strict-Transport-Security header"],
  ["hsts_subdomains", "HSTS subdomains", "Include subdomains in HSTS"],
];

const SECURITY_TOGGLES: readonly ToggleDef[] = [
  [
    "crowdsec_enabled",
    "CrowdSec protection",
    "Enforce CrowdSec bans (and the AppSec WAF) for this host at the edge",
  ],
];

function ToggleGrid({
  defs,
  values,
  disabled,
  onChange,
}: {
  defs: readonly ToggleDef[];
  values: Record<ToggleKey, boolean>;
  disabled: boolean;
  onChange: (key: ToggleKey, value: boolean) => void;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {defs.map(([key, label, hint]) => (
        <label key={key} className="flex items-start gap-2">
          <Switch
            aria-label={label}
            checked={values[key]}
            onCheckedChange={(v) => onChange(key, v)}
            disabled={disabled}
          />
          <span className="space-y-0.5">
            <span className="block text-sm font-medium leading-none">{label}</span>
            <span className="block text-xs text-muted-foreground">{hint}</span>
          </span>
        </label>
      ))}
    </div>
  );
}

export function ProxyHostDialog({
  open,
  onOpenChange,
  host,
  pools,
  pages,
  lists,
  certs,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  host?: ProxyHost | null;
  pools: Upstream[];
  pages: CustomPageSummary[];
  lists: AccessList[];
  certs: Certificate[];
  onSaved: () => void;
}) {
  const isEdit = Boolean(host);
  // Seeded from props on mount; the parent remounts this dialog (keyed) per
  // target, so no reset-on-open effect is needed.
  const [form, setForm] = useState<ProxyHostFormState>(() => stateFromHost(host));
  const [tab, setTab] = useState<DialogTab>("forwarding");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [domainsInvalid, setDomainsInvalid] = useState(false);

  function patch(changes: Partial<ProxyHostFormState>) {
    setForm((prev) => ({ ...prev, ...changes }));
  }

  function setToggle(key: ToggleKey, value: boolean) {
    setForm((prev) => ({ ...prev, toggles: { ...prev.toggles, [key]: value } }));
  }

  async function handleSubmit() {
    setError(null);
    if (domainsInvalid) {
      setError("Fix the highlighted domain first.");
      return;
    }
    const problem = validateForm(form);
    if (problem) {
      if (problem.tab) setTab(problem.tab);
      setError(problem.message);
      return;
    }
    const payload = buildPayload(form, host);

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
  const noCertificate = form.certificateId === NO_CERTIFICATE;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit proxy host" : "New proxy host"}</DialogTitle>
          <DialogDescription>
            Terminate domain names and forward matching traffic to upstream pools.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="host-domains">Domain names</Label>
            <DomainTagsInput
              id="host-domains"
              value={form.domains}
              onChange={(domains) => patch({ domains })}
              onPendingInvalidChange={setDomainsInvalid}
              placeholder="example.com"
              disabled={saving}
            />
            <p className="text-xs text-muted-foreground">
              Press Enter or comma after each domain. Wildcards like <code>*.example.com</code> are
              allowed.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="host-access-list">Access list</Label>
            <Select
              value={form.accessListId}
              onValueChange={(value) => patch({ accessListId: value as string })}
              items={{
                [NO_ACCESS_LIST]: "None (public)",
                ...Object.fromEntries(lists.map((l) => [String(l.id), l.name])),
              }}
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
          </div>

          <label className="flex items-start gap-2 self-end pb-2">
            <Switch
              aria-label="Enabled"
              checked={form.enabled}
              onCheckedChange={(v) => patch({ enabled: v })}
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

        <Tabs value={tab} onValueChange={(value) => setTab(value as DialogTab)}>
          <TabsList>
            <TabsTab value="forwarding">Forwarding</TabsTab>
            <TabsTab value="certificate">Certificate</TabsTab>
            <TabsTab value="advanced">Advanced</TabsTab>
          </TabsList>

          <TabsPanel value="forwarding" className="space-y-4 pt-2">
            <ToggleGrid
              defs={FORWARDING_TOGGLES}
              values={form.toggles}
              disabled={saving}
              onChange={setToggle}
            />
            <LocationsEditor
              rootTargetMode={form.rootTargetMode}
              rootUpstreamId={form.rootUpstreamId}
              rootForwardHost={form.rootForwardHost}
              rootForwardPort={form.rootForwardPort}
              rootScheme={form.rootScheme}
              onRootChange={patch}
              rows={form.locations}
              onRowsChange={(locations) => patch({ locations })}
              pools={pools}
              pages={pages}
              disabled={saving}
            />
          </TabsPanel>

          <TabsPanel value="certificate" className="space-y-4 pt-2">
            <div className="space-y-1.5">
              <Label htmlFor="host-certificate">Certificate</Label>
              <Select
                value={form.certificateId}
                onValueChange={(value) => patch({ certificateId: value as string })}
                items={{
                  [NO_CERTIFICATE]: "None (HTTP only)",
                  ...Object.fromEntries(certs.map((c) => [String(c.id), certLabel(c)])),
                }}
              >
                <SelectTrigger id="host-certificate" disabled={saving}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NO_CERTIFICATE}>None (HTTP only)</SelectItem>
                  {certs.map((cert) => (
                    <SelectItem
                      key={cert.id}
                      value={String(cert.id)}
                      disabled={cert.status !== "active"}
                    >
                      {certLabel(cert)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                Without a certificate the host serves plain HTTP on :80 and the options below have
                no effect.
              </p>
            </div>
            <ToggleGrid
              defs={TLS_TOGGLES}
              values={form.toggles}
              disabled={saving || noCertificate}
              onChange={setToggle}
            />
          </TabsPanel>

          <TabsPanel value="advanced" className="space-y-4 pt-2">
            <ToggleGrid
              defs={SECURITY_TOGGLES}
              values={form.toggles}
              disabled={saving}
              onChange={setToggle}
            />
            <div className="space-y-1.5">
              <Label htmlFor="host-advanced">Advanced nginx config</Label>
              <Textarea
                id="host-advanced"
                value={form.advancedConfig}
                onChange={(e) => patch({ advancedConfig: e.target.value })}
                placeholder="# Raw directives injected into the server block"
                className="font-mono text-xs"
                disabled={saving}
              />
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
          <Button onClick={handleSubmit} disabled={saving || noPools}>
            {saving ? "Saving…" : isEdit ? "Save changes" : "Create host"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
