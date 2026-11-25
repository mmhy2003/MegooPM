"use client";

import { useState } from "react";
import { toast } from "sonner";

import {
  staticSites,
  type AccessList,
  type Certificate,
  type StaticSite,
  type StaticSiteFolders,
} from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import {
  CertificateSelect,
  certificateIdFromValue,
  valueFromCertificateId,
} from "@/components/hosts/certificate-select";
import { DomainTagsInput } from "@/components/domains/domain-tags-input";
import { FOLDER_MAX_LENGTH, FOLDER_STATUS_TEXT, isValidFolder } from "@/components/static-sites/lib";
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
import { SearchableSelect } from "@/components/ui/searchable-select";
import { Textarea } from "@/components/ui/textarea";
import { ToggleRow } from "@/components/hosts/toggle-row";
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";

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
  [
    "crowdsec_enabled",
    "CrowdSec protection",
    "Refuse IPs CrowdSec has banned (and inspect requests with the AppSec WAF). While CrowdSec is unreachable this site refuses traffic.",
  ],
];

type ToggleKey =
  | "ssl_forced"
  | "http2_support"
  | "hsts_enabled"
  | "hsts_subdomains"
  | "block_exploits"
  | "crowdsec_enabled";

type DialogTab = "details" | "ssl" | "advanced";

/** The access-list picker's "no list" entry; ids are numeric, so it cannot clash. */
const NO_ACCESS_LIST = "none";

interface FormState {
  domains: string[];
  folder: string;
  accessListId: string;
  certificateId: string;
  enabled: boolean;
  advancedConfig: string;
  toggles: Record<ToggleKey, boolean>;
}

function emptyToggles(): Record<ToggleKey, boolean> {
  return {
    ssl_forced: false,
    http2_support: false,
    hsts_enabled: false,
    hsts_subdomains: false,
    block_exploits: false,
    // On by default, as the API defaults it.
    crowdsec_enabled: true,
  };
}

function stateFromSite(site: StaticSite | null | undefined): FormState {
  if (!site) {
    return {
      domains: [],
      folder: "",
      accessListId: NO_ACCESS_LIST,
      certificateId: valueFromCertificateId(null),
      enabled: true,
      advancedConfig: "",
      toggles: emptyToggles(),
    };
  }
  return {
    domains: [...site.domain_names],
    folder: site.folder,
    accessListId: site.access_list_id != null ? String(site.access_list_id) : NO_ACCESS_LIST,
    certificateId: valueFromCertificateId(site.certificate_id),
    enabled: site.enabled,
    advancedConfig: site.advanced_config,
    toggles: {
      ssl_forced: site.ssl_forced,
      http2_support: site.http2_support,
      hsts_enabled: site.hsts_enabled,
      hsts_subdomains: site.hsts_subdomains,
      block_exploits: site.block_exploits,
      crowdsec_enabled: site.crowdsec_enabled,
    },
  };
}

/** The folder picker's entries: every folder found under /sites, plus the
 *  site's saved folder even when it is gone, so editing never blanks it. */
function folderItems(folders: StaticSiteFolders, current: string): Record<string, string> {
  const items: Record<string, string> = {};
  for (const entry of folders.folders ?? []) {
    items[entry.path] = entry.has_index ? entry.path : `${entry.path} · no index.html`;
  }
  if (current && !(current in items)) items[current] = current;
  return items;
}

export function StaticSiteDialog({
  open,
  onOpenChange,
  site,
  certificates,
  lists,
  folders,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  site?: StaticSite | null;
  certificates: Certificate[];
  lists: AccessList[];
  folders: StaticSiteFolders;
  onSaved: () => void;
}) {
  const isEdit = Boolean(site);
  // Seeded from props on mount; the parent remounts this dialog (keyed) per
  // target, so neither the form nor the tab needs a reset-on-open effect.
  const [form, setForm] = useState<FormState>(() => stateFromSite(site));
  const [tab, setTab] = useState<DialogTab>("details");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [domainsInvalid, setDomainsInvalid] = useState(false);
  // With nothing to pick from (no mount, or an empty one) typing is the only way.
  const [typing, setTyping] = useState(() => (folders.folders ?? []).length === 0);

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
    if (domainsInvalid) {
      fail("Fix the highlighted domain first.");
      return;
    }
    if (form.domains.length === 0) {
      fail("Enter at least one domain name.");
      return;
    }
    const folder = form.folder.trim();
    if (!folder) {
      fail("Choose a folder.");
      return;
    }
    if (!isValidFolder(folder)) {
      fail(
        "Enter a folder inside the sites folder, like docs or clients/acme. Use letters, digits, " +
          "., _ and -; no part may start with a dot.",
      );
      return;
    }

    const payload = {
      domain_names: form.domains,
      folder,
      access_list_id:
        form.accessListId === NO_ACCESS_LIST ? null : Number.parseInt(form.accessListId, 10),
      certificate_id: certificateIdFromValue(form.certificateId),
      enabled: form.enabled,
      advanced_config: form.advancedConfig,
      ...form.toggles,
    };

    setSaving(true);
    try {
      const saved =
        isEdit && site
          ? await staticSites.update(site.id, payload)
          : await staticSites.create(payload);
      const verb = isEdit ? "updated" : "created";
      // Advisory only: the files can arrive after the site is saved.
      if (saved.folder_status !== "ok") {
        toast.warning(`Static site ${verb}. ${FOLDER_STATUS_TEXT[saved.folder_status]}.`);
      } else {
        toast.success(`Static site ${verb}`);
      }
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
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit static site" : "New static site"}</DialogTitle>
          <DialogDescription>
            Serve the files in a folder for a set of domains.
          </DialogDescription>
        </DialogHeader>

        {/* Domains identify the site, so they stay visible above the tabs. */}
        <div className="space-y-1.5">
          <Label htmlFor="site-domains">Domain names</Label>
          <DomainTagsInput
            id="site-domains"
            value={form.domains}
            onChange={(domains) => setForm((p) => ({ ...p, domains }))}
            onPendingInvalidChange={setDomainsInvalid}
            placeholder="www.example.com"
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
            <TabsTab value="advanced">Advanced</TabsTab>
          </TabsList>

          <TabsPanel value="details" className="space-y-4 pt-2">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <div className="flex items-center justify-between gap-2">
                  <Label htmlFor="site-folder">Folder</Label>
                  {(folders.folders ?? []).length > 0 ? (
                    <Button
                      type="button"
                      variant="link"
                      size="sm"
                      className="h-auto p-0 text-xs"
                      onClick={() => setTyping((t) => !t)}
                      disabled={saving}
                    >
                      {typing ? "Pick from the list" : "Type a folder path"}
                    </Button>
                  ) : null}
                </div>
                {typing ? (
                  <Input
                    id="site-folder"
                    value={form.folder}
                    onChange={(e) => setForm((p) => ({ ...p, folder: e.target.value }))}
                    placeholder="docs or clients/acme"
                    maxLength={FOLDER_MAX_LENGTH}
                    autoComplete="off"
                    spellCheck={false}
                    className="font-mono"
                    disabled={saving}
                  />
                ) : (
                  <SearchableSelect
                    id="site-folder"
                    aria-label="Folder"
                    value={form.folder}
                    onValueChange={(folder) => setForm((p) => ({ ...p, folder }))}
                    items={folderItems(folders, form.folder)}
                    placeholder="Choose a folder…"
                    searchLabel="Search folders"
                    disabled={saving}
                  />
                )}
                <p className="text-xs text-muted-foreground">
                  Folder inside the sites folder. Put a 404.html there to use your own error page.
                </p>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="site-access-list">Access list</Label>
                <SearchableSelect
                  id="site-access-list"
                  value={form.accessListId}
                  onValueChange={(value) => setForm((p) => ({ ...p, accessListId: value }))}
                  items={{
                    [NO_ACCESS_LIST]: "None (public)",
                    ...Object.fromEntries(lists.map((l) => [String(l.id), l.name])),
                  }}
                  searchLabel="Search access lists"
                  disabled={saving}
                />
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
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
                hint="Disabled sites are excluded from the nginx config"
                checked={form.enabled}
                onCheckedChange={(v) => setForm((p) => ({ ...p, enabled: v }))}
                disabled={saving}
              />
            </div>
          </TabsPanel>

          <TabsPanel value="ssl" className="space-y-4 pt-2">
            <CertificateSelect
              id="site-certificate"
              value={form.certificateId}
              onValueChange={(v) => setForm((p) => ({ ...p, certificateId: v }))}
              certificates={certificates}
              disabled={saving}
              hint="Optional — serve the site over HTTPS. Without one the options below have no effect."
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

          <TabsPanel value="advanced" className="space-y-1.5 pt-2">
            <Label htmlFor="site-advanced">Advanced nginx config</Label>
            <Textarea
              id="site-advanced"
              value={form.advancedConfig}
              onChange={(e) => setForm((p) => ({ ...p, advancedConfig: e.target.value }))}
              placeholder="# Raw directives injected into the server block"
              className="font-mono text-xs"
              disabled={saving}
            />
            <p className="text-xs text-muted-foreground">
              Directives go inside the site&apos;s <code>server {"{ }"}</code> block (both :80 and
              :443 when it has a certificate). nginx checks them with <code>nginx -t</code> and the
              change is rolled back if they fail.
            </p>
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
            {saving ? "Saving…" : isEdit ? "Save changes" : "Create site"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
