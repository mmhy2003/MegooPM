"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import { instanceSettings, type InstanceSettings, type LlmTestResult } from "@/lib/api";
import {
  buildLlmPayload,
  buildLlmTestPayload,
  describeError,
  llmStateFromSettings,
  validateLlmForm,
  type LlmFormState,
} from "@/components/settings/lib";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

/**
 * Configure the LLM integration.
 *
 * Owns its own state and save, so it stays independent of the Default site card
 * beside it. The API key is the awkward part: it is never returned, so the field
 * starts empty and reports whether one is stored rather than what it is.
 */
export function LlmCard({
  settings,
  onSaved,
}: {
  settings: InstanceSettings;
  onSaved: (settings: InstanceSettings) => void;
}) {
  const [form, setForm] = useState<LlmFormState>(() => llmStateFromSettings(settings));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<LlmTestResult | null>(null);

  function patch(changes: Partial<LlmFormState>) {
    setForm((current) => ({ ...current, ...changes }));
  }

  async function handleSave() {
    const problem = validateLlmForm(form);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const updated = await instanceSettings.updateLlm(buildLlmPayload(form));
      setForm(llmStateFromSettings(updated));
      toast.success("LLM settings saved");
      onSaved(updated);
    } catch (err) {
      const described = describeError(err);
      setError(described.message);
      toast.error(described.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setError(null);
    setResult(null);
    setTesting(true);
    try {
      // Sends what is in the form, so an unsaved key can be checked. The server
      // fills anything omitted from the stored row.
      setResult(await instanceSettings.testLlm(buildLlmTestPayload(form)));
    } catch (err) {
      // A failed *probe* comes back as ok:false with 200; reaching here means
      // the request itself failed.
      setError(describeError(err).message);
    } finally {
      setTesting(false);
    }
  }

  return (
    <section className="space-y-4 rounded-xl border p-5">
      <div>
        <h3 className="text-sm font-semibold">LLM Integration</h3>
        <p className="text-sm text-muted-foreground">
          Let MegooPM call a language model. Off by default — this opens outbound
          connections from your proxy to a third party.
        </p>
      </div>

      <label className="flex items-start gap-2">
        <Switch
          aria-label="Enable LLM features"
          checked={form.enabled}
          onCheckedChange={(enabled) => patch({ enabled })}
          disabled={saving}
        />
        <span className="space-y-0.5">
          <span className="block text-sm font-medium leading-none">Enable LLM features</span>
          <span className="block text-xs text-muted-foreground">
            Features that call the model stay inert until this is on.
          </span>
        </span>
      </label>

      <div className="space-y-1.5">
        <Label htmlFor="llm-model">Model</Label>
        <Input
          id="llm-model"
          value={form.model}
          onChange={(e) => patch({ model: e.target.value })}
          placeholder="gpt-4o"
          disabled={saving}
        />
        <p className="text-xs text-muted-foreground">
          The provider is part of the name — <code>anthropic/claude-sonnet-4</code>,{" "}
          <code>ollama/llama3</code>.
        </p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="llm-key">API key</Label>
        <div className="flex gap-2">
          <Input
            id="llm-key"
            type="password"
            value={form.apiKey}
            onChange={(e) => patch({ apiKey: e.target.value, keyCleared: false })}
            placeholder={form.keyIsSet ? "leave blank to keep" : "sk-…"}
            disabled={saving}
            className="flex-1"
          />
          {form.keyIsSet ? (
            <Button
              variant="outline"
              size="sm"
              disabled={saving}
              onClick={() => patch({ apiKey: "", keyIsSet: false, keyCleared: true })}
            >
              Remove stored key
            </Button>
          ) : null}
        </div>
        <p className="text-xs text-muted-foreground">
          {form.keyIsSet ? "A key is stored." : "No key stored."} Leave blank for a
          local model that needs none — in that case the provider library may fall
          back to an API key set in the environment.
        </p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="llm-base">API base</Label>
        <Input
          id="llm-base"
          value={form.apiBase}
          onChange={(e) => patch({ apiBase: e.target.value })}
          placeholder="optional — e.g. http://localhost:11434"
          disabled={saving}
        />
      </div>

      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}

      {result ? (
        result.ok ? (
          <p className="rounded-lg border border-success/30 bg-success/5 p-3 text-sm">
            <span className="font-medium">Connected.</span> {result.model} replied{" "}
            <span className="font-mono">{result.reply}</span> in {result.latency_ms} ms.
          </p>
        ) : (
          <p role="alert" className="text-sm text-destructive">
            {result.error}
          </p>
        )
      ) : null}

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={handleTest} disabled={testing || saving}>
          {testing ? <Loader2 className="animate-spin" /> : null}
          Test connection
        </Button>
        <Button onClick={handleSave} disabled={saving || testing}>
          {saving ? "Saving…" : "Save LLM settings"}
        </Button>
      </div>
    </section>
  );
}
