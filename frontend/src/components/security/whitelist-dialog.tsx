"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { crowdsec, type Whitelist, type WhitelistCreate } from "@/lib/api";

/** One entry per line; blank lines are how people group entries, not entries. */
function toEntries(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

const IPV4 = /^(\d{1,3}\.){3}\d{1,3}$/;

/**
 * Client-side check mirroring the server's message, so the operator sees the
 * same words whichever side rejects it. Deliberately narrow — it catches typos
 * and the server's `ipaddress` parse stays the authority. IPv6 is passed
 * straight through rather than half-validated here.
 */
function badIp(value: string): boolean {
  if (value.includes(":")) return false;
  if (!IPV4.test(value)) return true;
  return value.split(".").some((octet) => Number(octet) > 255);
}

function badCidr(value: string): boolean {
  const [addr, bits, ...rest] = value.split("/");
  if (rest.length || bits === undefined || !addr) return true;
  const max = addr.includes(":") ? 128 : 32;
  const n = Number(bits);
  if (!/^\d+$/.test(bits) || !Number.isInteger(n) || n < 0 || n > max) return true;
  return badIp(addr);
}

export function WhitelistDialog({
  open,
  onOpenChange,
  whitelist,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  whitelist: Whitelist | null;
  onSubmit: (body: WhitelistCreate) => Promise<void>;
}) {
  const [name, setName] = useState(whitelist?.name ?? "");
  const [reason, setReason] = useState(whitelist?.reason ?? "");
  const [description, setDescription] = useState(whitelist?.description ?? "");
  const [ipsText, setIpsText] = useState((whitelist?.ips ?? []).join("\n"));
  const [cidrsText, setCidrsText] = useState((whitelist?.cidrs ?? []).join("\n"));
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState("");
  const [saving, setSaving] = useState(false);

  const ips = toEntries(ipsText);
  const cidrs = toEntries(cidrsText);

  // Enough typed in for a preview to mean anything. Rendering is gated on this
  // rather than clearing `preview` from the effect body, which would be a
  // synchronous setState in an effect (react-hooks/set-state-in-effect).
  const canPreview = Boolean(name) && Boolean(reason) && ips.length + cidrs.length > 0;

  // The preview is rendered by the SERVER. Re-implementing the renderer here
  // would drift from the bytes that actually reach CrowdSec, and being those
  // bytes is the preview's whole point.
  useEffect(() => {
    const nextIps = toEntries(ipsText);
    const nextCidrs = toEntries(cidrsText);
    if (!name || !reason || (nextIps.length === 0 && nextCidrs.length === 0)) {
      return;
    }
    let cancelled = false;
    const handle = setTimeout(() => {
      crowdsec
        .previewWhitelist({
          name,
          reason,
          description,
          ips: nextIps,
          cidrs: nextCidrs,
          enabled: true,
        })
        .then((res) => {
          if (!cancelled) setPreview(res.yaml);
        })
        .catch(() => {
          // A preview failure is cosmetic: the operator can still save, and the
          // server validates again on the way in.
          if (!cancelled) setPreview("");
        });
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [name, reason, description, ipsText, cidrsText]);

  async function handleSave() {
    const offendingIp = ips.find(badIp);
    if (offendingIp) {
      setError(`'${offendingIp}' is not a valid IP address.`);
      return;
    }
    const offendingCidr = cidrs.find(badCidr);
    if (offendingCidr) {
      setError(`'${offendingCidr}' is not a valid CIDR range.`);
      return;
    }
    if (ips.length === 0 && cidrs.length === 0) {
      setError("A whitelist needs at least one IP address or CIDR range.");
      return;
    }
    setError(null);
    setSaving(true);
    try {
      await onSubmit({
        name,
        reason,
        description,
        ips,
        cidrs,
        enabled: whitelist?.enabled ?? true,
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{whitelist ? "Edit whitelist" : "Add whitelist"}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="wl-name">Name</Label>
            <Input id="wl-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="wl-reason">Reason</Label>
            <Input
              id="wl-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            <p className="text-muted-foreground text-xs">
              Shown in CrowdSec&apos;s own logs when this whitelist matches.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="wl-description">Description</Label>
            <Input
              id="wl-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="wl-ips">IP addresses</Label>
            <Textarea
              id="wl-ips"
              rows={3}
              value={ipsText}
              onChange={(e) => setIpsText(e.target.value)}
            />
            <p className="text-muted-foreground text-xs">One per line.</p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="wl-cidrs">CIDR ranges</Label>
            <Textarea
              id="wl-cidrs"
              rows={3}
              value={cidrsText}
              onChange={(e) => setCidrsText(e.target.value)}
            />
            <p className="text-muted-foreground text-xs">One per line.</p>
          </div>

          {error ? <p className="text-destructive text-sm">{error}</p> : null}

          {canPreview && preview ? (
            <div className="space-y-1.5">
              <Label>Rendered YAML</Label>
              <pre className="bg-muted overflow-x-auto rounded-md p-3 text-xs">
                {preview}
              </pre>
            </div>
          ) : null}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={saving}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
