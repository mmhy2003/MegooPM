"use client";

import { useState } from "react";
import { CircleCheck } from "lucide-react";
import { toast } from "sonner";

import { dnsCredentials, type DnsCredential } from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { credentialLabel } from "@/components/dns-providers/lib";
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

export function VerifyCredentialDialog({
  open,
  onOpenChange,
  credential,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  credential: DnsCredential | null;
}) {
  const [domain, setDomain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [verified, setVerified] = useState(false);
  const [busy, setBusy] = useState(false);

  async function verify() {
    if (!credential) return;
    if (!domain.trim()) {
      return setError("Enter a domain inside the zone these credentials manage.");
    }
    setError(null);
    setVerified(false);
    setBusy(true);
    try {
      await dnsCredentials.verify(credential.id, { domain: domain.trim() });
      setVerified(true);
      toast.success("Credentials verified — a probe TXT record was written and removed.");
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Verify DNS credentials</DialogTitle>
          <DialogDescription>
            {credential
              ? `Writes and removes a temporary _megoopm-verify TXT record using ${credentialLabel(credential)}.`
              : ""}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="verify-domain">Domain in the zone</Label>
            <Input
              id="verify-domain"
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              placeholder="example.com"
              disabled={busy}
            />
          </div>
          {verified ? (
            <p className="inline-flex items-center gap-2 text-sm text-success" role="status">
              <CircleCheck className="size-4" aria-hidden /> Verified
            </p>
          ) : null}
          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
            Close
          </Button>
          <Button onClick={verify} disabled={busy}>
            {busy ? "Verifying…" : "Verify"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
