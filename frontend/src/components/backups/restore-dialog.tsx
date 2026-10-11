"use client";

import { useState } from "react";
import { toast } from "sonner";

import { backups, type Backup } from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { formatBytes } from "@/components/backups/lib";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * The confirmation in front of the one action that replaces the instance.
 *
 * It says what will happen in plain sentences before the button is reachable,
 * and the button stays disabled until the operator types this instance's
 * hostname. A restore cannot be undone from inside the product: the typed
 * confirmation is the only thing between a mis-click and a replaced database.
 */
export function RestoreDialog({
  backup,
  hostname,
  open,
  onOpenChange,
  onStarted,
}: {
  backup: Backup;
  hostname: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onStarted: () => void;
}) {
  const [typed, setTyped] = useState("");
  const [starting, setStarting] = useState(false);
  const confirmed = typed.trim() === hostname;

  async function start() {
    setStarting(true);
    try {
      await backups.restore(backup.name);
      toast.success("Restore started");
      onStarted();
    } catch (err) {
      toast.error(describeError(err).message);
    } finally {
      setStarting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Restore from this backup?</DialogTitle>
          <DialogDescription>
            This replaces this instance with what the backup holds.
          </DialogDescription>
        </DialogHeader>

        <ul className="text-muted-foreground list-disc space-y-1 pl-5 text-sm">
          <li>The database is replaced — anything added since this backup is gone.</li>
          <li>Certificates and their private keys are replaced.</li>
          <li>
            Sign-ins since this backup stop working — the sessions table comes from the backup too,
            so you may have to sign in again.
          </li>
          <li>The admin interface is unavailable for the duration.</li>
          <li>nginx keeps serving traffic throughout, then reloads the restored config.</li>
        </ul>

        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
          <dt className="text-muted-foreground">Backup</dt>
          <dd className="font-mono text-xs break-all">{backup.name}</dd>
          <dt className="text-muted-foreground">Taken</dt>
          <dd>{new Date(backup.created_at).toLocaleString()}</dd>
          <dt className="text-muted-foreground">Size</dt>
          <dd className="tabular-nums">{formatBytes(backup.size_bytes)}</dd>
          <dt className="text-muted-foreground">Version</dt>
          <dd>{backup.app_version ?? "unknown"}</dd>
        </dl>

        <div className="space-y-1.5">
          <Label htmlFor="restore-confirm">Type {hostname} to confirm</Label>
          <Input
            id="restore-confirm"
            value={typed}
            autoComplete="off"
            onChange={(event) => setTyped(event.target.value)}
            disabled={starting}
          />
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={starting}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => void start()}
            disabled={!confirmed || starting}
          >
            {starting ? "Starting…" : "Restore"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
