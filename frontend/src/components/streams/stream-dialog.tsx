"use client";

import { useState } from "react";
import { toast } from "sonner";

import { streams, type Certificate, type Stream, type Upstream } from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { parsePort } from "@/components/streams/lib";
import {
  CertificateSelect,
  certificateIdFromValue,
  valueFromCertificateId,
} from "@/components/hosts/certificate-select";
import { ToggleRow } from "@/components/hosts/toggle-row";
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

type DialogTab = "details" | "ssl";

/** One label for a pool, so the option and the trigger cannot disagree. */
function poolLabel(pool: Upstream): string {
  return `${pool.name} — ${(pool.backends ?? []).length} backend(s)`;
}

/** Which kind of target the stream forwards to. Exactly one is ever sent. */
type TargetMode = "host" | "pool";

interface FormState {
  targetMode: TargetMode;
  upstreamId: string;
  incomingPort: string;
  forwardHost: string;
  forwardPort: string;
  tcpForwarding: boolean;
  udpForwarding: boolean;
  certificateId: string;
  enabled: boolean;
}

function stateFromStream(stream: Stream | null | undefined): FormState {
  if (!stream) {
    return {
      targetMode: "host",
      upstreamId: "",
      incomingPort: "",
      forwardHost: "",
      forwardPort: "",
      tcpForwarding: true,
      udpForwarding: false,
      certificateId: valueFromCertificateId(null),
      enabled: true,
    };
  }
  return {
    targetMode: stream.upstream_id != null ? "pool" : "host",
    upstreamId: stream.upstream_id != null ? String(stream.upstream_id) : "",
    incomingPort: String(stream.incoming_port),
    forwardHost: stream.forward_host ?? "",
    forwardPort: stream.forward_port == null ? "" : String(stream.forward_port),
    tcpForwarding: stream.tcp_forwarding,
    udpForwarding: stream.udp_forwarding,
    certificateId: valueFromCertificateId(stream.certificate_id),
    enabled: stream.enabled,
  };
}

export function StreamDialog({
  open,
  onOpenChange,
  stream,
  certificates,
  pools,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  stream?: Stream | null;
  certificates: Certificate[];
  pools: Upstream[];
  onSaved: () => void;
}) {
  const isEdit = Boolean(stream);
  // Seeded from props on mount; the parent remounts this dialog (keyed) per
  // target, so neither the form nor the tab needs a reset-on-open effect.
  const [form, setForm] = useState<FormState>(() => stateFromStream(stream));
  const [tab, setTab] = useState<DialogTab>("details");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // An http-only pool never renders into stream{}, so attaching one is a 422.
  const streamPools = pools.filter((p) => p.context === "stream" || p.context === "both");

  /** Report a problem and reveal the field it refers to. */
  function fail(message: string, on: DialogTab = "details") {
    setTab(on);
    setError(message);
  }

  async function handleSubmit() {
    setError(null);
    // Every check below concerns a Details field, so surface that tab with the
    // error — otherwise the operator reads a complaint about a hidden input.
    const incomingPort = parsePort(form.incomingPort);
    if (incomingPort === null) {
      fail("Incoming port must be between 1 and 65535.");
      return;
    }
    const usingPool = form.targetMode === "pool";
    if (usingPool && !form.upstreamId) {
      fail("Choose an upstream pool to forward to.");
      return;
    }
    if (!usingPool && !form.forwardHost.trim()) {
      fail("Enter a forward host.");
      return;
    }
    const forwardPort = usingPool ? null : parsePort(form.forwardPort);
    if (!usingPool && forwardPort === null) {
      fail("Forward port must be between 1 and 65535.");
      return;
    }
    if (!form.tcpForwarding && !form.udpForwarding) {
      fail("Enable at least one protocol (TCP or UDP).");
      return;
    }

    const payload = {
      incoming_port: incomingPort,
      // Exactly one target reaches the API; the other side is explicitly
      // nulled so switching mode on an existing stream clears the old value.
      forward_host: usingPool ? null : form.forwardHost.trim(),
      forward_port: usingPool ? null : forwardPort,
      upstream_id: usingPool ? Number.parseInt(form.upstreamId, 10) : null,
      tcp_forwarding: form.tcpForwarding,
      udp_forwarding: form.udpForwarding,
      certificate_id: certificateIdFromValue(form.certificateId),
      enabled: form.enabled,
    };

    setSaving(true);
    try {
      if (isEdit && stream) {
        await streams.update(stream.id, payload);
      } else {
        await streams.create(payload);
      }
      toast.success(isEdit ? "Stream updated" : "Stream created");
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
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit stream" : "New stream"}</DialogTitle>
          <DialogDescription>
            Forward raw TCP and/or UDP traffic from a listening port to a backend.
          </DialogDescription>
        </DialogHeader>

        <Tabs value={tab} onValueChange={(value) => setTab(value as DialogTab)}>
          <TabsList>
            <TabsTab value="details">Details</TabsTab>
            <TabsTab value="ssl">SSL</TabsTab>
          </TabsList>

          <TabsPanel value="details" className="space-y-4 pt-2">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="stream-incoming-port">Incoming port</Label>
                <Input
                  id="stream-incoming-port"
                  type="number"
                  inputMode="numeric"
                  min={1}
                  max={65535}
                  value={form.incomingPort}
                  onChange={(e) => setForm((p) => ({ ...p, incomingPort: e.target.value }))}
                  placeholder="e.g. 5432"
                  disabled={saving}
                />
                <p className="text-xs text-muted-foreground">Port nginx listens on.</p>
              </div>

              {form.targetMode === "host" ? (
              <div className="space-y-1.5">
                <Label htmlFor="stream-forward-port">Forward port</Label>
                <Input
                  id="stream-forward-port"
                  type="number"
                  inputMode="numeric"
                  min={1}
                  max={65535}
                  value={form.forwardPort}
                  onChange={(e) => setForm((p) => ({ ...p, forwardPort: e.target.value }))}
                  placeholder="e.g. 5432"
                  disabled={saving}
                />
              </div>
              ) : null}

              <div className="space-y-1.5 sm:col-span-2">
                {/* Both modes' values stay in form state, so flipping back and
                    forth never loses typed input; only the active one is sent. */}
                <span className="text-sm font-medium">Forward to</span>
                <div className="flex gap-4" role="radiogroup" aria-label="Forward to">
                  {(["host", "pool"] as const).map((mode) => (
                    <label key={mode} className="flex items-center gap-2 text-sm">
                      <input
                        type="radio"
                        name="stream-target"
                        aria-label={mode === "host" ? "Single host" : "Pool"}
                        checked={form.targetMode === mode}
                        onChange={() => setForm((p) => ({ ...p, targetMode: mode }))}
                        disabled={saving}
                      />
                      {mode === "host" ? "Single host" : "Pool"}
                    </label>
                  ))}
                </div>
              </div>

              {form.targetMode === "host" ? (
                <div className="space-y-1.5 sm:col-span-2">
                  <Label htmlFor="stream-forward-host">Forward host</Label>
                  <Input
                    id="stream-forward-host"
                    value={form.forwardHost}
                    onChange={(e) => setForm((p) => ({ ...p, forwardHost: e.target.value }))}
                    placeholder="db.internal or 10.0.0.5"
                    disabled={saving}
                  />
                </div>
              ) : (
                <div className="space-y-1.5 sm:col-span-2">
                  <Label htmlFor="stream-upstream">Upstream pool</Label>
                  <Select
                    value={form.upstreamId}
                    onValueChange={(v) => setForm((p) => ({ ...p, upstreamId: v as string }))}
                    items={Object.fromEntries(streamPools.map((p) => [String(p.id), poolLabel(p)]))}
                  >
                    <SelectTrigger id="stream-upstream" disabled={saving}>
                      <SelectValue placeholder="Choose a pool" />
                    </SelectTrigger>
                    <SelectContent>
                      {streamPools.map((pool) => (
                        <SelectItem key={pool.id} value={String(pool.id)}>
                          {poolLabel(pool)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">
                    Only pools whose context allows streams are listed.
                  </p>
                </div>
              )}
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <ToggleRow
                label="TCP"
                hint="Forward TCP on the incoming port"
                checked={form.tcpForwarding}
                onCheckedChange={(v) => setForm((p) => ({ ...p, tcpForwarding: v }))}
                disabled={saving}
              />
              <ToggleRow
                label="UDP"
                hint="Forward UDP on the incoming port"
                checked={form.udpForwarding}
                onCheckedChange={(v) => setForm((p) => ({ ...p, udpForwarding: v }))}
                disabled={saving}
              />
              <ToggleRow
                className="sm:col-span-2"
                label="Enabled"
                hint="Disabled streams are excluded from the nginx config"
                checked={form.enabled}
                onCheckedChange={(v) => setForm((p) => ({ ...p, enabled: v }))}
                disabled={saving}
              />
            </div>
          </TabsPanel>

          <TabsPanel value="ssl" className="space-y-4 pt-2">
            {/* Raw TCP/UDP has no Force SSL / HSTS / HTTP2 equivalent, so the
                certificate is the whole of this tab. */}
            <CertificateSelect
              id="stream-certificate"
              value={form.certificateId}
              onValueChange={(v) => setForm((p) => ({ ...p, certificateId: v }))}
              certificates={certificates}
              disabled={saving}
              noneLabel="None (plain TCP/UDP)"
              hint="Optional — terminate TLS on the incoming TCP listener."
            />
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
            {saving ? "Saving…" : isEdit ? "Save changes" : "Create stream"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
