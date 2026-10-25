"use client";

import { useState } from "react";
import { FileUp } from "lucide-react";

import { instanceSettings, type AppSecBodyAction, type CrowdSecWafStatus } from "@/lib/api";
import { ConfirmDeleteDialog } from "@/components/proxy-hosts/confirm-delete-dialog";
import {
  ACTION_COPY,
  ACTIONS,
  MEMORY_NOTE_MB,
  describeWafRun,
  formatMb,
  fromMb,
  toMb,
  type SizeUnit,
} from "@/components/security/waf-lib";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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

const UNIT_ITEMS: Record<SizeUnit, string> = { MB: "MB", GB: "GB" };
const TITLE = "flex flex-wrap items-center gap-2 text-sm leading-none font-medium";
const TAG = "text-muted-foreground rounded-full border px-2 py-0.5 text-[11px] font-normal";

const CONFIRM =
  "CrowdSec restarts and protected hosts deny traffic for a few seconds. If CrowdSec doesn't come back, the previous limit is restored.";
const CONFIRM_BLOCKED =
  " CrowdSec's servers are blocking this address right now, so the restart may switch the community blocklist off to bring CrowdSec back.";

export function WafSettingsCard({
  status,
  onChanged,
}: {
  status: CrowdSecWafStatus;
  onChanged: () => void;
}) {
  const initial = fromMb(status.max_body_mb);
  const [value, setValue] = useState(initial.value);
  const [unit, setUnit] = useState<SizeUnit>(initial.unit);
  const [action, setAction] = useState<AppSecBodyAction>(status.body_exceeded_action);
  const [confirming, setConfirming] = useState(false);

  const mb = toMb(value, unit);
  const state = describeWafRun(status);
  const limit = mb === null ? "limit" : formatMb(mb);
  const hosts = status.protected_hosts;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <FileUp className="size-4" /> WAF upload limit
        </CardTitle>
        <CardDescription>
          The WAF reads each request body into memory to check it, up to this size. It applies to
          every host protected by CrowdSec. Saving restarts CrowdSec, and protected hosts deny
          traffic for a few seconds.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <p className="text-muted-foreground text-sm">
          {hosts.length === 0 ? (
            "No hosts use CrowdSec yet."
          ) : (
            <>
              Applies to {hosts.length} {hosts.length === 1 ? "host" : "hosts"}:{" "}
              {hosts.map((h, i) => (
                <span key={`${h.kind}-${h.id}`}>
                  {i > 0 ? ", " : ""}
                  <span className="text-foreground font-medium">{h.name}</span>
                </span>
              ))}
            </>
          )}
        </p>
        {!status.appsec_configured ? (
          <p className="text-muted-foreground text-sm">
            AppSec is off (CROWDSEC_APPSEC_URL is blank): the limit is saved but has no effect.
          </p>
        ) : null}

        <div className="grid gap-4 sm:grid-cols-[minmax(0,15rem)_minmax(0,1fr)]">
          <div className="space-y-1.5">
            <Label htmlFor="waf-size">Maximum body size</Label>
            <div className="flex gap-2">
              <Input
                id="waf-size"
                inputMode="decimal"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                className="w-28"
                aria-invalid={mb === null}
              />
              <Select
                value={unit}
                onValueChange={(v) => setUnit(v as SizeUnit)}
                items={UNIT_ITEMS}
              >
                <SelectTrigger aria-label="Size unit" className="w-20">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="MB">MB</SelectItem>
                  <SelectItem value="GB">GB</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {mb === null ? (
              <p className="text-destructive text-xs">
                Enter a size between 1 MB and 2 GB, in whole megabytes.
              </p>
            ) : (
              <p className="text-muted-foreground text-xs">CrowdSec&apos;s default is 10 MB.</p>
            )}
          </div>

          <div className="space-y-1.5">
            <p className="text-sm font-medium">When a request is bigger than this</p>
            <RadioGroup
              aria-label="When a request is bigger than the limit"
              value={action}
              // base-ui passes (value, eventDetails); only the first is the value.
              onValueChange={(v) => setAction(v as AppSecBodyAction)}
            >
              {ACTIONS.map((a) => {
                const copy = ACTION_COPY[a];
                return (
                  <label key={a} className="flex items-start gap-2.5 rounded-lg border p-3">
                    {/* Named by the wrapping <label>; see ban-page-card. */}
                    <Radio value={a} className="mt-0.5" />
                    <span className="space-y-1">
                      <span className={TITLE}>
                        {copy.title}
                        <span className={TAG}>{copy.tag}</span>
                      </span>
                      <span className="text-muted-foreground block text-xs">
                        {copy.explain(limit)}
                      </span>
                      <span className="flex flex-wrap gap-x-4 text-xs">
                        <span className="text-emerald-600 dark:text-emerald-400">
                          ✓ {copy.gain}
                        </span>
                        <span className="text-red-600 dark:text-red-400">✗ {copy.cost}</span>
                      </span>
                    </span>
                  </label>
                );
              })}
            </RadioGroup>
          </div>
        </div>

        {mb !== null && mb > MEMORY_NOTE_MB ? (
          <p className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm">
            Checking this much of a body takes several times that in memory: on CrowdSec 1.8.1,
            checking 100 MB of one upload used about 800 MB. Several large uploads at once can
            run this server out of memory.
          </p>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4">
          <p className={state.failed ? "text-destructive text-sm" : "text-sm"}>{state.label}</p>
          <Button
            onClick={() => setConfirming(true)}
            disabled={mb === null || status.running || !status.reload_configured}
          >
            Save and restart CrowdSec
          </Button>
        </div>
        {!status.reload_configured ? (
          <p className="text-destructive text-xs">
            CrowdSec reloads are not configured: set CROWDSEC_CONTROL_NODE_ID to the node whose
            worker has the docker socket.
          </p>
        ) : null}
      </CardContent>

      <ConfirmDeleteDialog
        open={confirming}
        onOpenChange={setConfirming}
        title="Restart CrowdSec to apply?"
        description={
          CONFIRM + (status.capi_enabled && status.capi_blocked ? CONFIRM_BLOCKED : "")
        }
        confirmLabel="Save and restart"
        successMessage="Applying…"
        onConfirm={async () => {
          if (mb === null) return;
          await instanceSettings.updateCrowdSecWaf({
            max_body_mb: mb,
            body_exceeded_action: action,
          });
        }}
        onDeleted={() => {
          setConfirming(false);
          onChanged();
        }}
      />
    </Card>
  );
}
