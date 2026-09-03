"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { toast } from "sonner";

import {
  instanceSettings,
  type CrowdSecJobRun,
  type HubUpdateFrequency,
  type InstanceSettings,
} from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { ConfirmDeleteDialog } from "@/components/proxy-hosts/confirm-delete-dialog";
import {
  describeHubRun,
  localHourToUtc,
  utcHourToLocal,
  WEEKDAYS,
} from "@/components/security/updates-lib";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

const HOURS = Array.from({ length: 24 }, (_, h) => h);

function pad(h: number): string {
  return `${String(h).padStart(2, "0")}:00`;
}

// base-ui's SelectValue shows the raw value unless the root knows the labels.
const FREQUENCY_LABELS: Record<HubUpdateFrequency, string> = { daily: "Daily", weekly: "Weekly" };
const WEEKDAY_ITEMS: Record<string, string> = Object.fromEntries(
  WEEKDAYS.map((n, i) => [String(i), n]),
);
const HOUR_ITEMS: Record<string, string> = Object.fromEntries(
  HOURS.map((h) => [String(h), pad(h)]),
);

export function HubUpdatesCard({
  settings,
  run,
  running,
  reloadConfigured,
  onSaved,
  onQueued,
  onUpdateNow,
}: {
  settings: InstanceSettings;
  run: CrowdSecJobRun | null;
  running: boolean;
  reloadConfigured: boolean;
  onSaved: (next: InstanceSettings) => void;
  onQueued: () => void;
  onUpdateNow: () => Promise<void>;
}) {
  const [auto, setAuto] = useState(settings.crowdsec_hub_auto_update);
  const [frequency, setFrequency] = useState<HubUpdateFrequency>(
    settings.crowdsec_hub_update_frequency,
  );
  const [weekday, setWeekday] = useState(settings.crowdsec_hub_update_weekday);
  const [hourLocal, setHourLocal] = useState(utcHourToLocal(settings.crowdsec_hub_update_hour_utc));
  const [saving, setSaving] = useState(false);
  const [confirm, setConfirm] = useState(false);

  const hourUtc = localHourToUtc(hourLocal);
  const dirty =
    auto !== settings.crowdsec_hub_auto_update ||
    frequency !== settings.crowdsec_hub_update_frequency ||
    weekday !== settings.crowdsec_hub_update_weekday ||
    hourUtc !== settings.crowdsec_hub_update_hour_utc;

  async function save() {
    setSaving(true);
    try {
      const next = await instanceSettings.updateCrowdSecHub({
        auto_update: auto,
        frequency,
        weekday,
        hour_utc: hourUtc,
      });
      toast.success("Schedule saved");
      onSaved(next);
    } catch (err) {
      toast.error(describeError(err).message);
    } finally {
      setSaving(false);
    }
  }

  const agent = typeof run?.detail.agent_version === "string" ? run.detail.agent_version : null;
  const latest =
    typeof run?.detail.latest_agent_version === "string" ? run.detail.latest_agent_version : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <RefreshCw className="size-4" /> Detection rules
        </CardTitle>
        <CardDescription>
          CrowdSec&apos;s parsers, scenarios and AppSec rules come from its hub and only refresh
          when the container starts. This keeps them current. If a refresh changes anything,
          CrowdSec restarts and protected hosts deny traffic for a few seconds.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <label className="flex items-center justify-between gap-3 text-sm">
          <span>Update detection rules automatically</span>
          <Switch
            checked={auto}
            onCheckedChange={(v) => setAuto(Boolean(v))}
            aria-label="Update detection rules automatically"
            disabled={saving}
          />
        </label>
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="space-y-1.5">
            <Label htmlFor="hub-frequency">Frequency</Label>
            <Select
              value={frequency}
              onValueChange={(v) => setFrequency(v as HubUpdateFrequency)}
              items={FREQUENCY_LABELS}
            >
              <SelectTrigger id="hub-frequency" disabled={!auto || saving}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="daily">Daily</SelectItem>
                <SelectItem value="weekly">Weekly</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {frequency === "weekly" ? (
            <div className="space-y-1.5">
              <Label htmlFor="hub-weekday">Day</Label>
              <Select
                value={String(weekday)}
                onValueChange={(v) => setWeekday(Number(v))}
                items={WEEKDAY_ITEMS}
              >
                <SelectTrigger id="hub-weekday" disabled={!auto || saving}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {WEEKDAYS.map((name, i) => (
                    <SelectItem key={name} value={String(i)}>
                      {name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : null}
          <div className="space-y-1.5">
            <Label htmlFor="hub-hour">Time</Label>
            <Select
              value={String(hourLocal)}
              onValueChange={(v) => setHourLocal(Number(v))}
              items={HOUR_ITEMS}
            >
              <SelectTrigger id="hub-hour" disabled={!auto || saving}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {HOURS.map((h) => (
                  <SelectItem key={h} value={String(h)}>
                    {pad(h)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-muted-foreground text-xs">Your time. {pad(hourUtc)} UTC.</p>
          </div>
        </div>

        <div className="space-y-1 text-sm">
          <p>{describeHubRun(run)}</p>
          {agent ? (
            <p className="text-muted-foreground text-xs">
              CrowdSec {agent}
              {latest && latest !== agent
                ? ` — ${latest} is available; rules that need it are skipped until the image is updated.`
                : ""}
            </p>
          ) : null}
          {!reloadConfigured ? (
            <p className="text-destructive text-xs">
              CrowdSec reloads are not configured: set CROWDSEC_CONTROL_NODE_ID to the node whose
              worker has the docker socket.
            </p>
          ) : null}
        </div>
      </CardContent>
      <CardFooter className="justify-between gap-2">
        <Button
          variant="outline"
          onClick={() => setConfirm(true)}
          disabled={running || !reloadConfigured}
        >
          <RefreshCw /> {running ? "Running…" : "Update now"}
        </Button>
        <Button onClick={() => void save()} disabled={!dirty || saving}>
          {saving ? "Saving…" : "Save schedule"}
        </Button>
      </CardFooter>

      <ConfirmDeleteDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="Update now"
        description="This checks the CrowdSec hub for newer rules. If anything changed, CrowdSec restarts and protected hosts deny traffic for a few seconds."
        confirmLabel="Update now"
        successMessage="Update queued"
        onConfirm={onUpdateNow}
        onDeleted={onQueued}
      />
    </Card>
  );
}
