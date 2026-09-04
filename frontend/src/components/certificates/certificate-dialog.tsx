"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
  ACME_CHALLENGES,
  certificates,
  dnsCredentials,
  type AcmeChallenge,
  type DnsCredential,
} from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { letsEncryptPayload } from "@/components/certificates/lib";
import { credentialLabel } from "@/components/dns-providers/lib";
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
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

const CHALLENGE_LABELS: Record<AcmeChallenge, string> = {
  "http-01": "HTTP-01 (webroot)",
  "dns-01": "DNS-01 (wildcards)",
};

/** A newly-enqueued async job the parent should poll for completion. */
export interface PendingTask {
  taskId: string;
  label: string;
}

export function CertificateDialog({
  open,
  onOpenChange,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called after a successful create; `pending` is set for async issuance. */
  onSaved: (pending?: PendingTask) => void;
}) {
  // Let's Encrypt fields
  const [leName, setLeName] = useState("");
  const [leDomains, setLeDomains] = useState<string[]>([]);
  const [leDomainsInvalid, setLeDomainsInvalid] = useState(false);
  const [challenge, setChallenge] = useState<AcmeChallenge>("http-01");
  const [accountEmail, setAccountEmail] = useState("");
  const [dnsCredentialId, setDnsCredentialId] = useState("");
  const [dnsOptions, setDnsOptions] = useState<DnsCredential[]>([]);
  const [dnsOptionsError, setDnsOptionsError] = useState<string | null>(null);

  // Saved DNS credentials for the DNS-01 picker. The dialog is mounted only
  // while open, so this runs once per opening; state is set after the await.
  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const list = await dnsCredentials.list();
        if (active) setDnsOptions(list);
      } catch (err) {
        if (active) setDnsOptionsError(describeError(err).message);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  // Custom upload fields
  const [customName, setCustomName] = useState("");
  const [certPem, setCertPem] = useState("");
  const [keyPem, setKeyPem] = useState("");
  const [chainPem, setChainPem] = useState("");

  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submitLetsEncrypt() {
    setError(null);
    if (leDomainsInvalid) return setError("Fix the highlighted domain first.");
    const result = letsEncryptPayload({
      name: leName,
      domains: leDomains,
      challenge,
      accountEmail,
      dnsCredentialId,
    });
    if (!result.ok) return setError(result.error);

    setSaving(true);
    try {
      const issued = await certificates.requestLetsEncrypt(result.body);
      toast.success("Issuance requested — this can take a minute.");
      onOpenChange(false);
      onSaved({ taskId: issued.task_id, label: `Issuing “${issued.certificate.name}”` });
    } catch (err) {
      const described = describeError(err);
      setError(described.message);
      toast.error(described.message);
    } finally {
      setSaving(false);
    }
  }

  async function submitCustom() {
    setError(null);
    if (!customName.trim()) return setError("Give the certificate a name.");
    if (!certPem.trim()) return setError("Paste the leaf certificate (PEM).");
    if (!keyPem.trim()) return setError("Paste the matching private key (PEM).");

    setSaving(true);
    try {
      await certificates.uploadCustom({
        name: customName.trim(),
        certificate_pem: certPem,
        private_key_pem: keyPem,
        chain_pem: chainPem.trim() || null,
      });
      toast.success("Certificate uploaded");
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
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>New certificate</DialogTitle>
          <DialogDescription>
            Request a certificate from Let&apos;s Encrypt or upload your own PEM material.
          </DialogDescription>
        </DialogHeader>

        <Tabs defaultValue="letsencrypt">
          <TabsList>
            <TabsTab value="letsencrypt">Let&apos;s Encrypt</TabsTab>
            <TabsTab value="custom">Upload custom</TabsTab>
          </TabsList>

          {/* ---- Let's Encrypt ---- */}
          <TabsPanel value="letsencrypt" className="space-y-4 pt-2">
            <div className="space-y-1.5">
              <Label htmlFor="le-name">Name</Label>
              <Input
                id="le-name"
                value={leName}
                onChange={(e) => setLeName(e.target.value)}
                placeholder="prod-wildcard"
                disabled={saving}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="le-domains">Domain names</Label>
              <DomainTagsInput
                id="le-domains"
                value={leDomains}
                onChange={setLeDomains}
                onPendingInvalidChange={setLeDomainsInvalid}
                placeholder="example.com"
                disabled={saving}
              />
              <p className="text-xs text-muted-foreground">
                Press Enter or comma after each domain. DNS-01 is required for wildcards like{" "}
                <code>*.example.com</code>.
              </p>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="le-challenge">Challenge</Label>
                <Select
                  value={challenge}
                  onValueChange={(v) => setChallenge(v as AcmeChallenge)}
                  items={CHALLENGE_LABELS}
                >
                  <SelectTrigger id="le-challenge" disabled={saving}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ACME_CHALLENGES.map((c) => (
                      <SelectItem key={c} value={c}>
                        {CHALLENGE_LABELS[c]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="le-email">Account email (optional)</Label>
                <Input
                  id="le-email"
                  type="email"
                  value={accountEmail}
                  onChange={(e) => setAccountEmail(e.target.value)}
                  placeholder="ops@example.com"
                  disabled={saving}
                />
              </div>
            </div>

            {challenge === "dns-01" ? (
              <div className="space-y-1.5">
                <Label htmlFor="le-dns-credential">DNS credentials</Label>
                <Select
                  value={dnsCredentialId}
                  onValueChange={(v) => setDnsCredentialId((v as string) ?? "")}
                  items={Object.fromEntries(
                    dnsOptions.map((c) => [String(c.id), credentialLabel(c)]),
                  )}
                >
                  <SelectTrigger
                    id="le-dns-credential"
                    disabled={saving || dnsOptions.length === 0}
                  >
                    <SelectValue placeholder="Choose saved credentials" />
                  </SelectTrigger>
                  <SelectContent>
                    {dnsOptions.map((c) => (
                      <SelectItem key={c.id} value={String(c.id)}>
                        {credentialLabel(c)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  {dnsOptionsError
                    ? `Couldn’t load DNS credentials: ${dnsOptionsError}`
                    : dnsOptions.length === 0
                      ? "No DNS provider credentials yet — add one under Certificates → DNS providers."
                      : "The provider's API is used to publish the _acme-challenge TXT record."}
                </p>
              </div>
            ) : null}

            {error ? (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            ) : null}

            <DialogFooter>
              <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
                Cancel
              </Button>
              <Button onClick={submitLetsEncrypt} disabled={saving}>
                {saving ? "Requesting…" : "Request certificate"}
              </Button>
            </DialogFooter>
          </TabsPanel>

          {/* ---- Custom upload ---- */}
          <TabsPanel value="custom" className="space-y-4 pt-2">
            <div className="space-y-1.5">
              <Label htmlFor="custom-name">Name</Label>
              <Input
                id="custom-name"
                value={customName}
                onChange={(e) => setCustomName(e.target.value)}
                placeholder="acme-corp-2026"
                disabled={saving}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="custom-cert">Certificate (PEM)</Label>
              <Textarea
                id="custom-cert"
                value={certPem}
                onChange={(e) => setCertPem(e.target.value)}
                placeholder="-----BEGIN CERTIFICATE-----"
                className="min-h-24 font-mono text-xs"
                disabled={saving}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="custom-key">Private key (PEM)</Label>
              <Textarea
                id="custom-key"
                value={keyPem}
                onChange={(e) => setKeyPem(e.target.value)}
                placeholder="-----BEGIN PRIVATE KEY-----"
                className="min-h-24 font-mono text-xs"
                disabled={saving}
              />
              <p className="text-xs text-muted-foreground">
                Stored write-only — the key is never returned by the API.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="custom-chain">Intermediate chain (PEM, optional)</Label>
              <Textarea
                id="custom-chain"
                value={chainPem}
                onChange={(e) => setChainPem(e.target.value)}
                placeholder="-----BEGIN CERTIFICATE-----"
                className="min-h-16 font-mono text-xs"
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
              <Button onClick={submitCustom} disabled={saving}>
                {saving ? "Uploading…" : "Upload certificate"}
              </Button>
            </DialogFooter>
          </TabsPanel>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
