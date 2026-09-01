"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Settings as SettingsIcon } from "lucide-react";
import { toast } from "sonner";

import {
  customPages,
  instanceSettings,
  type CustomPageSummary,
  type DefaultSiteMode,
} from "@/lib/api";
import {
  DEFAULT_SITE_MODES,
  DEFAULT_SITE_MODE_HINTS,
  DEFAULT_SITE_MODE_LABELS,
  buildDefaultSitePayload,
  describeError,
  emptyFormState,
  stateFromSettings,
  validateSettingsForm,
  type SettingsFormState,
} from "@/components/settings/lib";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Radio, RadioGroup } from "@/components/ui/radio-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Instance configuration. Today it holds one card: the default site — what
 * nginx returns for a request matching no configured host.
 */
export function SettingsView() {
  const router = useRouter();
  const [form, setForm] = useState<SettingsFormState>(emptyFormState);
  const [saved, setSaved] = useState<SettingsFormState>(emptyFormState);
  const [pages, setPages] = useState<CustomPageSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // Both are needed to render the card: the picker's options are as much
      // part of the form as the setting itself.
      const [settings, list] = await Promise.all([instanceSettings.get(), customPages.list()]);
      setForm(stateFromSettings(settings));
      setSaved(stateFromSettings(settings));
      setPages(list);
      setLoadError(null);
    } catch (err) {
      setLoadError(describeError(err).message);
    } finally {
      setLoading(false);
    }
  }, []);

  // The IIFE keeps the effect callback itself synchronous; `load` awaits before
  // any setState, so nothing updates state synchronously in the effect body.
  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  const dirty =
    form.mode !== saved.mode ||
    form.redirectUrl !== saved.redirectUrl ||
    form.pageId !== saved.pageId;

  async function handleSave() {
    const problem = validateSettingsForm(form);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const updated = await instanceSettings.update(buildDefaultSitePayload(form));
      setSaved(stateFromSettings(updated));
      setForm(stateFromSettings(updated));
      toast.success("Default site saved");
    } catch (err) {
      // 422 → the backend's stricter URL rules, or an unknown page id.
      const described = describeError(err);
      setError(described.message);
      toast.error(described.message);
    } finally {
      setSaving(false);
    }
  }

  if (loadError) {
    return (
      <div className="mx-auto flex max-w-3xl flex-col items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
        <p className="text-sm text-destructive" role="alert">
          Couldn&apos;t load settings: {loadError}
        </p>
        <Button variant="outline" size="sm" onClick={() => void load()}>
          Retry
        </Button>
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <SettingsIcon className="size-5" />
        </div>
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Settings</h2>
          <p className="text-sm text-muted-foreground">Instance configuration.</p>
        </div>
      </div>

      <section className="space-y-4 rounded-xl border p-5">
        <div>
          <h3 className="text-sm font-semibold">Default site</h3>
          <p className="text-sm text-muted-foreground">
            What to serve for a request that matches no configured host.
          </p>
        </div>

        {loading ? (
          <div className="space-y-3">
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className="h-5 w-1/2" />
            <Skeleton className="h-5 w-3/5" />
          </div>
        ) : (
          <>
            <RadioGroup
              value={form.mode}
              // base-ui passes (value, eventDetails) — the second argument is
              // ignored here but must not be mistaken for the value.
              onValueChange={(value) =>
                setForm((current) => ({ ...current, mode: value as DefaultSiteMode }))
              }
            >
              {DEFAULT_SITE_MODES.map((mode) => (
                <label key={mode} className="flex items-start gap-2.5">
                  <Radio
                    value={mode}
                    aria-label={DEFAULT_SITE_MODE_LABELS[mode]}
                    disabled={saving}
                    className="mt-0.5"
                  />
                  <span className="space-y-0.5">
                    <span className="block text-sm font-medium leading-none">
                      {DEFAULT_SITE_MODE_LABELS[mode]}
                    </span>
                    <span className="block text-xs text-muted-foreground">
                      {DEFAULT_SITE_MODE_HINTS[mode]}
                    </span>
                  </span>
                </label>
              ))}
            </RadioGroup>

            {form.mode === "redirect" ? (
              <div className="space-y-1.5">
                <Label htmlFor="ds-url">Redirect to</Label>
                <Input
                  id="ds-url"
                  value={form.redirectUrl}
                  onChange={(e) =>
                    setForm((current) => ({ ...current, redirectUrl: e.target.value }))
                  }
                  placeholder="https://example.com"
                  disabled={saving}
                />
              </div>
            ) : null}

            {form.mode === "custom_page" ? (
              pages.length === 0 ? (
                <div className="flex flex-col items-start gap-2 rounded-lg border border-dashed p-4">
                  <p className="text-sm text-muted-foreground">You have no custom pages yet.</p>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => router.push("/custom-pages/new")}
                  >
                    Create a page
                  </Button>
                </div>
              ) : (
                <div className="space-y-1.5">
                  <Label htmlFor="ds-page">Page to serve</Label>
                  <Select
                    value={form.pageId === null ? "" : String(form.pageId)}
                    onValueChange={(value) =>
                      setForm((current) => ({ ...current, pageId: Number(value) }))
                    }
                  >
                    <SelectTrigger id="ds-page" disabled={saving}>
                      <SelectValue placeholder="Choose a page" />
                    </SelectTrigger>
                    <SelectContent>
                      {pages.map((page) => (
                        <SelectItem key={page.id} value={String(page.id)}>
                          {page.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )
            ) : null}

            {error ? (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            ) : null}

            <div className="flex justify-end">
              <Button onClick={handleSave} disabled={saving || !dirty}>
                {saving ? "Saving…" : "Save changes"}
              </Button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
