"use client";

import { useState } from "react";
import { toast } from "sonner";

import {
  instanceSettings,
  type CustomPageSummary,
  type InstanceSettings,
  type MaintenancePageMode,
} from "@/lib/api";
import { describeError } from "@/components/settings/lib";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Radio, RadioGroup } from "@/components/ui/radio-group";
import { SearchableSelect } from "@/components/ui/searchable-select";

const MODES: MaintenancePageMode[] = ["megoopm", "custom_page"];

const LABELS: Record<MaintenancePageMode, string> = {
  megoopm: "MegooPM page",
  custom_page: "Custom page",
};

const HINTS: Record<MaintenancePageMode, string> = {
  megoopm: "A branded page saying the site will be back shortly.",
  custom_page: "One of your custom pages.",
};

// The API's bounds, mirrored so the button refuses what the server would.
const MIN_MINUTES = 1;
const MAX_MINUTES = 10080;

/**
 * Chooses what a visitor sees on a host switched into maintenance.
 *
 * There is no "no page" mode, unlike the ban page: a host under maintenance
 * must answer something, and a bare 503 tells a visitor nothing.
 */
export function MaintenanceCard({
  settings,
  pages,
  onSaved,
}: {
  settings: InstanceSettings;
  pages: CustomPageSummary[];
  onSaved: (row: InstanceSettings) => void;
}) {
  const [mode, setMode] = useState<MaintenancePageMode>(settings.maintenance_mode);
  const [pageId, setPageId] = useState<number | null>(settings.maintenance_page_id);
  // Held as text so the field can be cleared while typing; a number state would
  // snap an empty field back to 0 and fight the operator mid-edit.
  const [minutes, setMinutes] = useState(String(settings.maintenance_retry_after_minutes));
  const [saving, setSaving] = useState(false);

  const parsedMinutes = Number(minutes);
  const minutesUsable =
    minutes.trim() !== "" &&
    Number.isInteger(parsedMinutes) &&
    parsedMinutes >= MIN_MINUTES &&
    parsedMinutes <= MAX_MINUTES;

  // Nothing to save until something differs from what is stored: a live button
  // on an unchanged form invites a PATCH that writes back what is already
  // there, and tells the operator nothing about whether their edit took.
  const dirty =
    mode !== settings.maintenance_mode ||
    pageId !== settings.maintenance_page_id ||
    parsedMinutes !== settings.maintenance_retry_after_minutes;

  async function handleSave() {
    setSaving(true);
    try {
      const row = await instanceSettings.updateMaintenance({
        mode,
        // Never send a page the mode does not use: the API rejects it, because
        // the payload would describe two configurations at once.
        page_id: mode === "custom_page" ? pageId : null,
        retry_after_minutes: parsedMinutes,
      });
      onSaved(row);
      toast.success("Maintenance page saved");
    } catch (error) {
      toast.error(describeError(error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="bg-card text-card-foreground space-y-4 rounded-xl border p-4 shadow-xs">
      <div className="space-y-1">
        <h3 className="text-sm font-semibold">Maintenance page</h3>
        <p className="text-muted-foreground text-sm">
          What a visitor sees on a host you have switched to maintenance. It is served with a 503
          and a Retry-After header, so search engines come back rather than de-indexing the site.
        </p>
      </div>

      <RadioGroup
        aria-label="Maintenance page"
        value={mode}
        onValueChange={(value) => {
          const next = value as MaintenancePageMode;
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
          <Label htmlFor="maintenance-page">Page to serve</Label>
          <SearchableSelect
            id="maintenance-page"
            value={pageId === null ? "" : String(pageId)}
            onValueChange={(value) => setPageId(Number(value))}
            items={Object.fromEntries(pages.map((page) => [String(page.id), page.name]))}
            placeholder="Choose a page"
            disabled={saving}
            searchLabel="Search pages"
          />
          <p className="text-muted-foreground text-xs">
            Editing the page itself takes effect on the next configuration change.
          </p>
        </div>
      ) : null}

      <div className="space-y-1.5">
        <Label htmlFor="maintenance-retry">Retry after (minutes)</Label>
        <Input
          id="maintenance-retry"
          type="number"
          min={MIN_MINUTES}
          max={MAX_MINUTES}
          className="max-w-40"
          value={minutes}
          onChange={(event) => setMinutes(event.target.value)}
          disabled={saving}
        />
        <p className="text-muted-foreground text-xs">
          How long the page tells a crawler to wait before trying again. A week is the longest that
          still says anything useful.
        </p>
      </div>

      <div className="flex justify-end">
        <Button
          onClick={handleSave}
          disabled={
            saving || !dirty || !minutesUsable || (mode === "custom_page" && pageId === null)
          }
        >
          {saving ? "Saving…" : "Save maintenance page"}
        </Button>
      </div>
    </section>
  );
}
