"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import {
  instanceSettings,
  type InstanceSettings,
  type MailTestResult,
  type SmtpSecurity,
} from "@/lib/api";
import {
  buildSmtpPayload,
  describeError,
  smtpStateFromSettings,
  validateSmtpForm,
  type SmtpFormState,
} from "@/components/settings/lib";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const SECURITY_LABELS: Record<SmtpSecurity, string> = {
  starttls: "STARTTLS (usually port 587)",
  ssl: "TLS from connect (usually port 465)",
  none: "None — trusted local relay only",
};

/**
 * Configure outbound email.
 *
 * Owns its own state and save, like the cards beside it. The password is the
 * awkward part: it is never returned, so the field starts empty and the card
 * reports whether one is stored rather than what it is.
 */
export function SmtpCard({
  settings,
  onSaved,
}: {
  settings: InstanceSettings;
  onSaved: (settings: InstanceSettings) => void;
}) {
  const [form, setForm] = useState<SmtpFormState>(() => smtpStateFromSettings(settings));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<MailTestResult | null>(null);

  function patch(changes: Partial<SmtpFormState>) {
    setForm((current) => ({ ...current, ...changes }));
  }

  async function handleSave() {
    const problem = validateSmtpForm(form);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const updated = await instanceSettings.updateSmtp(buildSmtpPayload(form));
      setForm(smtpStateFromSettings(updated));
      toast.success("Email settings saved");
      onSaved(updated);
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setError(null);
    setResult(null);
    setTesting(true);
    try {
      // Deliberately no recipient: the backend sends it to the signed-in admin,
      // which is what "does my mail server work" almost always means.
      setResult(await instanceSettings.testSmtp({}));
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setTesting(false);
    }
  }

  return (
    <section className="space-y-4 rounded-xl border p-4">
      <div className="space-y-1">
        <h3 className="text-sm font-semibold">Email</h3>
        <p className="text-muted-foreground text-sm">
          The mail server MegooPM sends from. Save, then send yourself a test.
        </p>
      </div>

      <div className="flex items-center gap-2">
        <Switch
          id="smtp-enabled"
          checked={form.enabled}
          onCheckedChange={(next) => patch({ enabled: next })}
          aria-label="Send email"
          disabled={saving}
        />
        <Label htmlFor="smtp-enabled">Send email</Label>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="smtp-host">Host</Label>
          <Input
            id="smtp-host"
            value={form.host}
            onChange={(e) => patch({ host: e.target.value })}
            placeholder="smtp.example.com"
            disabled={saving}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="smtp-port">Port</Label>
          <Input
            id="smtp-port"
            inputMode="numeric"
            value={form.port}
            onChange={(e) => patch({ port: e.target.value })}
            disabled={saving}
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="smtp-security">Security</Label>
        <Select
          value={form.security}
          onValueChange={(value) => patch({ security: value as SmtpSecurity })}
        >
          <SelectTrigger id="smtp-security" disabled={saving}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(Object.keys(SECURITY_LABELS) as SmtpSecurity[]).map((value) => (
              <SelectItem key={value} value={value}>
                {SECURITY_LABELS[value]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="smtp-username">Username</Label>
          <Input
            id="smtp-username"
            value={form.username}
            onChange={(e) => patch({ username: e.target.value })}
            placeholder="optional"
            disabled={saving}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="smtp-password">Password</Label>
          <div className="flex gap-2">
            <Input
              id="smtp-password"
              type="password"
              aria-label="Password"
              value={form.password}
              onChange={(e) => patch({ password: e.target.value, passwordCleared: false })}
              placeholder={form.passwordIsSet ? "leave blank to keep" : "optional"}
              disabled={saving}
              className="flex-1"
            />
            {form.passwordIsSet ? (
              <Button
                variant="outline"
                size="sm"
                disabled={saving}
                onClick={() =>
                  patch({ password: "", passwordIsSet: false, passwordCleared: true })
                }
              >
                Remove
              </Button>
            ) : null}
          </div>
          <p className="text-muted-foreground text-xs">
            {form.passwordIsSet ? "A password is stored." : "No password stored."}
          </p>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="smtp-from">From address</Label>
          <Input
            id="smtp-from"
            value={form.from}
            onChange={(e) => patch({ from: e.target.value })}
            placeholder="megoopm@example.com"
            disabled={saving}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="smtp-from-name">From name</Label>
          <Input
            id="smtp-from-name"
            value={form.fromName}
            onChange={(e) => patch({ fromName: e.target.value })}
            placeholder="MegooPM"
            disabled={saving}
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="smtp-app-url">This instance&apos;s public URL</Label>
        <Input
          id="smtp-app-url"
          value={form.appUrl}
          onChange={(e) => patch({ appUrl: e.target.value })}
          placeholder="https://pm.example.com"
          disabled={saving}
        />
        <p className="text-muted-foreground text-xs">
          Not used yet. Password-reset and invitation links will be built from it.
        </p>
      </div>

      {error ? (
        <p role="alert" className="text-destructive text-sm">
          {error}
        </p>
      ) : null}

      {result ? (
        result.ok ? (
          <p className="border-success/30 bg-success/5 rounded-lg border p-3 text-sm">
            <span className="font-medium">Sent.</span> {result.detail} ({result.latency_ms} ms)
          </p>
        ) : (
          <p role="alert" className="text-destructive text-sm">
            {result.detail}
          </p>
        )
      ) : null}

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={handleTest} disabled={testing || saving}>
          {testing ? <Loader2 className="animate-spin" /> : null}
          Send test email
        </Button>
        <Button onClick={handleSave} disabled={saving || testing}>
          {saving ? "Saving…" : "Save email settings"}
        </Button>
      </div>
    </section>
  );
}
