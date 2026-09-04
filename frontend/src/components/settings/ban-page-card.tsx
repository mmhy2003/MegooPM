"use client";

import { useState } from "react";
import { toast } from "sonner";

import {
  instanceSettings,
  type CrowdSecBanMode,
  type CustomPageSummary,
  type InstanceSettings,
} from "@/lib/api";
import { describeError } from "@/components/settings/lib";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Radio, RadioGroup } from "@/components/ui/radio-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const MODES: CrowdSecBanMode[] = ["megoopm", "custom_page", "none"];

const LABELS: Record<CrowdSecBanMode, string> = {
  megoopm: "MegooPM page",
  custom_page: "Custom page",
  none: "No page",
};

const HINTS: Record<CrowdSecBanMode, string> = {
  megoopm: "A branded page explaining the request was blocked.",
  custom_page: "One of your custom pages.",
  none: "A bare 403, which does not advertise what is in front.",
};

/**
 * Chooses what a CrowdSec-blocked visitor is served.
 *
 * Its own card and its own PATCH, like the other settings groups: the coherence
 * rule ("custom_page needs a page") can only be checked against a payload that
 * carries the mode.
 */
export function BanPageCard({
  settings,
  pages,
  onSaved,
}: {
  settings: InstanceSettings;
  pages: CustomPageSummary[];
  onSaved: (row: InstanceSettings) => void;
}) {
  const [mode, setMode] = useState<CrowdSecBanMode>(settings.crowdsec_ban_mode);
  const [pageId, setPageId] = useState<number | null>(settings.crowdsec_ban_page_id);
  const [saving, setSaving] = useState(false);

  // Nothing to save until something differs from what is stored. A live button
  // on an unchanged form invites a PATCH that writes back the values already
  // there, and tells the operator nothing about whether their edit took.
  const dirty = mode !== settings.crowdsec_ban_mode || pageId !== settings.crowdsec_ban_page_id;

  async function handleSave() {
    setSaving(true);
    try {
      const row = await instanceSettings.updateBanPage({
        crowdsec_ban_mode: mode,
        // Never send a page the mode does not use: the API clears it anyway,
        // and sending one makes the payload describe two configurations.
        crowdsec_ban_page_id: mode === "custom_page" ? pageId : null,
      });
      onSaved(row);
      toast.success("Ban page saved");
    } catch (error) {
      toast.error(describeError(error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="space-y-4 rounded-xl border p-4">
      <div className="space-y-1">
        <h3 className="text-sm font-semibold">Ban page</h3>
        <p className="text-muted-foreground text-sm">
          What a visitor blocked by CrowdSec is served. Applies to every host.
        </p>
      </div>

      <RadioGroup
        aria-label="Ban page"
        value={mode}
        // base-ui passes (value, eventDetails); the second argument is ignored
        // here but must not be mistaken for the value.
        onValueChange={(value) => {
          const next = value as CrowdSecBanMode;
          setMode(next);
          // Reset at the transition that invalidates the selection rather than
          // in an effect, which eslint forbids and which would run a frame late.
          if (next !== "custom_page") setPageId(null);
        }}
      >
        {MODES.map((value) => (
          <label key={value} className="flex items-start gap-2.5">
            {/* No aria-label: the wrapping <label> already names this, and
                base-ui's aria-labelledby wins over aria-label, so setting both
                makes a screen reader announce the label twice. */}
            <Radio value={value} disabled={saving} className="mt-0.5" />
            <span className="space-y-0.5">
              <span className="block text-sm leading-none font-medium">{LABELS[value]}</span>
              <span className="text-muted-foreground block text-xs">{HINTS[value]}</span>
            </span>
          </label>
        ))}
      </RadioGroup>

      {mode === "custom_page" ? (
        <div className="space-y-1.5">
          <Label htmlFor="ban-page">Page to serve</Label>
          <Select
            value={pageId === null ? "" : String(pageId)}
            onValueChange={(value) => setPageId(Number(value))}
            items={Object.fromEntries(pages.map((page) => [String(page.id), page.name]))}
          >
            <SelectTrigger id="ban-page" disabled={saving}>
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
          <p className="text-muted-foreground text-xs">
            Editing the page itself takes effect on the next configuration change.
          </p>
        </div>
      ) : null}

      <div className="flex justify-end">
        <Button
          onClick={handleSave}
          disabled={saving || !dirty || (mode === "custom_page" && pageId === null)}
        >
          {saving ? "Saving…" : "Save ban page"}
        </Button>
      </div>
    </section>
  );
}
