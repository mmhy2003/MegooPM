"use client";

import { useEffect, useState } from "react";
import { TriangleAlert } from "lucide-react";

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  crowdsec,
  WHITELIST_KIND_LABELS,
  WHITELIST_KINDS,
  type Whitelist,
  type WhitelistCreate,
  type WhitelistKind,
} from "@/lib/api";

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
 *
 * There is no equivalent for expressions: only CrowdSec can compile those.
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
  const [kind, setKind] = useState<WhitelistKind>(whitelist?.kind ?? "ip_cidr");
  const [name, setName] = useState(whitelist?.name ?? "");
  const [reason, setReason] = useState(whitelist?.reason ?? "");
  const [description, setDescription] = useState(whitelist?.description ?? "");
  const [ipsText, setIpsText] = useState((whitelist?.ips ?? []).join("\n"));
  const [cidrsText, setCidrsText] = useState((whitelist?.cidrs ?? []).join("\n"));
  const [filter, setFilter] = useState(whitelist?.filter ?? "");
  const [expressionsText, setExpressionsText] = useState(
    (whitelist?.expressions ?? []).join("\n"),
  );
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState("");
  const [saving, setSaving] = useState(false);

  const isExpression = kind === "expression";
  const ips = toEntries(ipsText);
  const cidrs = toEntries(cidrsText);
  const expressions = toEntries(expressionsText);

  const hasContent = isExpression
    ? expressions.length > 0
    : ips.length + cidrs.length > 0;
  // Enough typed in for a preview to mean anything. Rendering is gated on this
  // rather than clearing `preview` from the effect body, which would be a
  // synchronous setState in an effect (react-hooks/set-state-in-effect).
  const canPreview = Boolean(name) && Boolean(reason) && hasContent;

  // The preview is rendered by the SERVER. Re-implementing the renderer here
  // would drift from the bytes that actually reach CrowdSec, and being those
  // bytes is the preview's whole point.
  useEffect(() => {
    if (!canPreview) return;
    let cancelled = false;
    // Only the active kind's fields are sent. The API rejects a payload
    // carrying the other kind's fields rather than ignoring them, because
    // CrowdSec evaluates every key it finds — an `ip:` left on an expression
    // whitelist would quietly widen it.
    const payload: WhitelistCreate = isExpression
      ? {
          name,
          kind,
          reason,
          description,
          enabled: true,
          ips: [],
          cidrs: [],
          filter: filter || null,
          expressions,
        }
      : {
          name,
          kind,
          reason,
          description,
          enabled: true,
          ips,
          cidrs,
          filter: null,
          expressions: [],
        };
    const handle = setTimeout(() => {
      crowdsec
        .previewWhitelist(payload)
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
  }, [
    canPreview,
    isExpression,
    kind,
    name,
    reason,
    description,
    ipsText,
    cidrsText,
    filter,
    expressionsText,
    ips,
    cidrs,
    expressions,
  ]);

  async function handleSave() {
    if (isExpression) {
      if (expressions.length === 0) {
        setError("An expression whitelist needs at least one expression.");
        return;
      }
    } else {
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
    }
    setError(null);
    setSaving(true);
    try {
      await onSubmit(
        isExpression
          ? {
              name,
              kind,
              reason,
              description,
              enabled: whitelist?.enabled ?? true,
              ips: [],
              cidrs: [],
              filter: filter || null,
              expressions,
            }
          : {
              name,
              kind,
              reason,
              description,
              enabled: whitelist?.enabled ?? true,
              ips,
              cidrs,
              filter: null,
              expressions: [],
            },
      );
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
            <Label htmlFor="wl-kind">Kind</Label>
            <Select
              value={kind}
              onValueChange={(v) => setKind(v as WhitelistKind)}
              items={WHITELIST_KIND_LABELS}
            >
              <SelectTrigger id="wl-kind">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {WHITELIST_KINDS.map((k) => (
                  <SelectItem key={k} value={k}>
                    {WHITELIST_KIND_LABELS[k]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

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

          {isExpression ? (
            <>
              <div className="border-destructive/40 bg-destructive/10 flex items-start gap-3 rounded-lg border p-3">
                <TriangleAlert className="text-destructive mt-0.5 size-4 shrink-0" />
                <p className="text-xs">
                  Expressions are compiled by CrowdSec, not here, so a mistake
                  cannot be caught before you save. One that does not compile
                  stops CrowdSec starting — the apply detects that and rolls
                  back, but every protected host is briefly denied while it does.
                </p>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="wl-filter">Filter (optional)</Label>
                <Input
                  id="wl-filter"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  placeholder="evt.Meta.service == 'http'"
                />
                <p className="text-muted-foreground text-xs">
                  Scopes which events the expressions run against. Leave empty to
                  evaluate every event.
                </p>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="wl-expressions">Expressions</Label>
                <Textarea
                  id="wl-expressions"
                  rows={4}
                  value={expressionsText}
                  onChange={(e) => setExpressionsText(e.target.value)}
                  placeholder="evt.Meta.http_verb == 'GET' && evt.Meta.http_path == '/health'"
                />
                <p className="text-muted-foreground text-xs">
                  One per line. Fields come from the parsed event —{" "}
                  <code>evt.Meta.http_path</code>, <code>evt.Meta.http_verb</code>,{" "}
                  <code>evt.Meta.log_type</code>, <code>evt.Meta.service</code>.
                </p>
              </div>
            </>
          ) : (
            <>
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
            </>
          )}

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
