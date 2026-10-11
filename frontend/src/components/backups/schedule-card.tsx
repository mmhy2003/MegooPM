"use client";

import { useState } from "react";
import { CalendarClock } from "lucide-react";
import { toast } from "sonner";

import { instanceSettings, type InstanceSettings } from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { WEEKDAYS } from "@/components/security/updates-lib";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type Frequency = InstanceSettings["backup_frequency"];

const HOURS = Array.from({ length: 24 }, (_, h) => h);

function pad(h: number): string {
  return `${String(h).padStart(2, "0")}:00`;
}

// base-ui's SelectValue shows the raw value unless the root knows the labels.
const FREQUENCY_LABELS: Record<Frequency, string> = {
  off: "Never",
  daily: "Daily",
  weekly: "Weekly",
};
const WEEKDAY_ITEMS: Record<string, string> = Object.fromEntries(
  WEEKDAYS.map((name, i) => [String(i), name]),
);
const HOUR_ITEMS: Record<string, string> = Object.fromEntries(
  HOURS.map((h) => [String(h), pad(h)]),
);

/**
 * When automatic backups run, and how many to keep.
 *
 * The hour is UTC and labelled as such rather than converted, because the
 * server's schedule is what an operator reasons about when they compare this
 * with a backup's file name — the names are UTC too.
 */
export function ScheduleCard({
  settings,
  onSaved,
}: {
  settings: InstanceSettings | null;
  onSaved: (next: InstanceSettings) => void;
}) {
  const [frequency, setFrequency] = useState<Frequency>(settings?.backup_frequency ?? "off");
  const [hour, setHour] = useState(settings?.backup_hour_utc ?? 3);
  const [weekday, setWeekday] = useState(settings?.backup_weekday ?? 6);
  const [keep, setKeep] = useState(settings?.backup_keep ?? 7);
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      const next = await instanceSettings.updateBackups({
        frequency,
        hour_utc: hour,
        weekday,
        keep,
      });
      onSaved(next);
      toast.success("Backup schedule saved");
    } catch (err) {
      toast.error(describeError(err).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <CalendarClock className="size-4" aria-hidden="true" /> Automatic backups
        </CardTitle>
        <CardDescription>
          A backup runs on the schedule below and is kept on this server. Manual backups are kept
          until you delete them — only automatic ones are swept.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 sm:grid-cols-4">
        <div className="space-y-1.5">
          <Label htmlFor="backup-frequency">How often</Label>
          <Select
            value={frequency}
            onValueChange={(v) => setFrequency(v as Frequency)}
            items={FREQUENCY_LABELS}
          >
            <SelectTrigger id="backup-frequency" disabled={saving}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="off">Never</SelectItem>
              <SelectItem value="daily">Daily</SelectItem>
              <SelectItem value="weekly">Weekly</SelectItem>
            </SelectContent>
          </Select>
        </div>
        {frequency === "weekly" ? (
          <div className="space-y-1.5">
            <Label htmlFor="backup-weekday">Day of the week</Label>
            <Select
              value={String(weekday)}
              onValueChange={(v) => setWeekday(Number(v))}
              items={WEEKDAY_ITEMS}
            >
              <SelectTrigger id="backup-weekday" disabled={saving}>
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
          <Label htmlFor="backup-hour">Time (UTC)</Label>
          <Select value={String(hour)} onValueChange={(v) => setHour(Number(v))} items={HOUR_ITEMS}>
            <SelectTrigger id="backup-hour" disabled={frequency === "off" || saving}>
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
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="backup-keep">Keep the last</Label>
          <Input
            id="backup-keep"
            type="number"
            min={1}
            max={365}
            value={keep}
            onChange={(event) => setKeep(Number(event.target.value))}
            disabled={saving}
          />
        </div>
      </CardContent>
      <CardFooter className="justify-end">
        <Button onClick={() => void save()} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </CardFooter>
    </Card>
  );
}
