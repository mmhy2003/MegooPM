"use client";

import { TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { WhitelistApplyStatus } from "@/lib/api";

/**
 * Why a whitelist may not actually be in force.
 *
 * Saving a whitelist returns 200 long before it reaches CrowdSec: the row goes
 * to the database, then a task on the control-plane node writes the parser file
 * and restarts the container. Both later steps can fail, and if CrowdSec does
 * not come back the file is rolled back.
 *
 * Without this banner the table would show a whitelist that reads as active
 * while CrowdSec has never seen it — the same invisible-failure shape as the
 * LAPI timeout that stringified to an empty message.
 */
export function WhitelistStatusBanner({
  status,
  onRetry,
}: {
  status: WhitelistApplyStatus;
  onRetry: () => void;
}) {
  if (status.reload_configured && status.ok) return null;

  const message = !status.reload_configured
    ? "Whitelists are saved but never applied: CROWDSEC_CONTROL_NODE_ID is not set, so no node is designated to restart CrowdSec."
    : (status.error ?? "The last whitelist apply failed.");

  return (
    <div className="border-destructive/40 bg-destructive/10 flex items-start gap-3 rounded-lg border p-3">
      <TriangleAlert className="text-destructive mt-0.5 size-4 shrink-0" />
      <div className="flex-1 space-y-2">
        <p className="text-sm">{message}</p>
        {status.reload_configured ? (
          <Button size="sm" variant="outline" onClick={onRetry}>
            Retry apply
          </Button>
        ) : null}
      </div>
    </div>
  );
}
