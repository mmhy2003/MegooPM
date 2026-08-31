"use client";

import { useState } from "react";
import { toast } from "sonner";

import { streams, type Certificate, type Stream } from "@/lib/api";
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
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";

type DialogTab = "details" | "ssl";

interface FormState {
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
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  stream?: Stream | null;
  certificates: Certificate[];
  onSaved: () => void;
}) {
  const isEdit = Boolean(stream);
  // Seeded from props on mount; the parent remounts this dialog (keyed) per
  // target, so neither the form nor the tab needs a reset-on-open effect.
  const [form, setForm] = useState<FormState>(() => stateFromStream(stream));
  const [tab, setTab] = useState<DialogTab>("details");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

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
    if (!form.forwardHost.trim()) {
      fail("Enter a forward host.");
      return;
    }
    const forwardPort = parsePort(form.forwardPort);
    if (forwardPort === null) {
      fail("Forward port must be between 1 and 65535.");
      return;
    }
    if (!form.tcpForwarding && !form.udpForwarding) {
      fail("Enable at least one protocol (TCP or UDP).");
      return;
    }

    const payload = {
      incoming_port: incomingPort,
      forward_host: form.forwardHost.trim(),
      forward_port: forwardPort,
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
      <DialogContent className="max-h-[90vh] max-w-lg overflow-y-auto">
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
