"use client";

import { useState } from "react";
import { Globe } from "lucide-react";
import { toast } from "sonner";

import { instanceSettings, type CrowdSecJobRun } from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { ConfirmDeleteDialog } from "@/components/proxy-hosts/confirm-delete-dialog";
import { describeCapiRun } from "@/components/security/updates-lib";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";

export function BlocklistCard({
  desired,
  run,
  running,
  reloadConfigured,
  onChanged,
}: {
  desired: boolean;
  run: CrowdSecJobRun | null;
  running: boolean;
  reloadConfigured: boolean;
  onChanged: () => void;
}) {
  const [pending, setPending] = useState<boolean | null>(null);
  const state = describeCapiRun(desired, run, running);

  async function apply(enabled: boolean) {
    await instanceSettings.updateCrowdSecCapi({ enabled });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Globe className="size-4" /> Community blocklist
        </CardTitle>
        <CardDescription>
          CrowdSec&apos;s shared threat intelligence: addresses reported by other CrowdSec users are
          blocked here too, and this instance&apos;s alerts are shared back. Once on, it refreshes
          itself every two hours.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <label className="flex items-center justify-between gap-3 text-sm">
          <span>Use the CrowdSec community blocklist</span>
          <Switch
            checked={desired}
            onCheckedChange={(v) => setPending(Boolean(v))}
            aria-label="Use the CrowdSec community blocklist"
            disabled={running || !reloadConfigured}
          />
        </label>
        <p className={state.failed ? "text-destructive text-sm" : "text-muted-foreground text-sm"}>
          {state.label}
        </p>
        {state.failed ? (
          <div>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                apply(desired)
                  .then(() => {
                    toast.success("Retrying…");
                    onChanged();
                  })
                  .catch((err: unknown) => toast.error(describeError(err).message));
              }}
            >
              Retry
            </Button>
          </div>
        ) : null}
        {!reloadConfigured ? (
          <p className="text-destructive text-xs">
            CrowdSec reloads are not configured: set CROWDSEC_CONTROL_NODE_ID to the node whose
            worker has the docker socket.
          </p>
        ) : null}
      </CardContent>

      <ConfirmDeleteDialog
        open={pending !== null}
        onOpenChange={(open) => {
          if (!open) setPending(null);
        }}
        title={pending ? "Turn on the community blocklist?" : "Turn off the community blocklist?"}
        description={
          pending
            ? "CrowdSec restarts and protected hosts deny traffic for a few seconds. This registers this instance with CrowdSec's central service."
            : "CrowdSec restarts and protected hosts deny traffic for a few seconds."
        }
        confirmLabel={pending ? "Turn on" : "Turn off"}
        successMessage="Applying…"
        onConfirm={() => apply(pending === true)}
        onDeleted={() => {
          setPending(null);
          onChanged();
        }}
      />
    </Card>
  );
}
