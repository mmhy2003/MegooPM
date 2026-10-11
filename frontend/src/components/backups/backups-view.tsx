"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Archive, Download, RotateCcw, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";

import {
  backups,
  instanceSettings,
  type Backup,
  type InstanceSettings,
  type RestoreStatus,
} from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { formatBytes, triggerLabel } from "@/components/backups/lib";
import { ScheduleCard } from "@/components/backups/schedule-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { RowCard, RowCards } from "@/components/ui/row-cards";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useIsMobile } from "@/hooks/use-mobile";
import { subscribeToEvents } from "@/lib/events";

/** The floor under the event stream, as elsewhere: push is an accelerator. */
const POLL_MS = 60_000;

function whenLabel(iso: string): string {
  return new Date(iso).toLocaleString();
}

/** The last automatic or manual run, in one line the page can always show. */
function LastRun({ settings }: { settings: InstanceSettings | null }) {
  if (!settings || settings.last_backup_ok === null || !settings.last_backup_at) {
    return <p className="text-muted-foreground text-sm">No backup has run yet.</p>;
  }
  const when = whenLabel(settings.last_backup_at);
  if (settings.last_backup_ok) {
    return <p className="text-muted-foreground text-sm">Last backup succeeded ({when}).</p>;
  }
  return (
    <p className="text-destructive text-sm">
      Last backup failed ({when}): {settings.last_backup_message ?? "no reason recorded"}
    </p>
  );
}

/**
 * The Backups page: take one, schedule them, and restore from one.
 *
 * The list is the backup directory itself, so a file an operator copies onto
 * the server appears here and one they delete disappears. A backup made under
 * a different SECRET_KEY is listed but cannot be restored — it would fail, and
 * the secrets inside it would be unreadable — so the row says so instead of
 * offering a button that cannot work.
 */
export function BackupsView() {
  const isMobile = useIsMobile();
  const [items, setItems] = useState<Backup[]>([]);
  const [freeBytes, setFreeBytes] = useState<number | null>(null);
  const [settings, setSettings] = useState<InstanceSettings | null>(null);
  const [restore, setRestore] = useState<RestoreStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Backup | null>(null);
  const [pendingRestore, setPendingRestore] = useState<Backup | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const [list, stored, status] = await Promise.all([
        backups.list(),
        instanceSettings.get(),
        backups.restoreStatus(),
      ]);
      setItems(list.items);
      setFreeBytes(list.free_bytes);
      setSettings(stored);
      setRestore(status);
      setError(null);
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Wrapped, like the dashboard's: a bare `void load()` here calls setState
    // synchronously inside the effect body, which React flags as a cascading
    // render.
    void (async () => {
      await load();
    })();
    const timer = setInterval(() => void load(), POLL_MS);
    const unsubscribe = subscribeToEvents((type) => {
      if (type.startsWith("backup.") || type.startsWith("restore.")) void load();
    });
    return () => {
      clearInterval(timer);
      unsubscribe();
    };
  }, [load]);

  async function takeBackup() {
    setBusy(true);
    try {
      await backups.create();
      toast.success("Backup started");
    } catch (err) {
      toast.error(describeError(err).message);
    } finally {
      setBusy(false);
    }
  }

  async function upload(file: File) {
    setBusy(true);
    try {
      await backups.upload(file);
      toast.success("Backup uploaded");
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : describeError(err).message);
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function download(backup: Backup) {
    try {
      await backups.download(backup.name);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : describeError(err).message);
    }
  }

  async function confirmDelete() {
    const target = pendingDelete;
    if (!target) return;
    setPendingDelete(null);
    try {
      await backups.remove(target.name);
      toast.success("Backup deleted");
      await load();
    } catch (err) {
      toast.error(describeError(err).message);
    }
  }

  async function startRestore(backup: Backup) {
    setPendingRestore(null);
    try {
      await backups.restore(backup.name);
      toast.success("Restore started");
      await load();
    } catch (err) {
      toast.error(describeError(err).message);
    }
  }

  const empty = error
    ? "The backups could not be listed."
    : "No backups yet. Take one before you need one.";

  const rowActions = (backup: Backup) => (
    <div className="flex flex-wrap justify-end gap-1">
      <Button variant="ghost" size="sm" onClick={() => void download(backup)}>
        <Download aria-hidden="true" />
        Download
      </Button>
      <Button
        variant="ghost"
        size="sm"
        disabled={!backup.key_matches || restore?.running === true}
        onClick={() => setPendingRestore(backup)}
      >
        <RotateCcw aria-hidden="true" />
        Restore
      </Button>
      <Button variant="ghost-destructive" size="sm" onClick={() => setPendingDelete(backup)}>
        <Trash2 aria-hidden="true" />
        Delete
      </Button>
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <div className="bg-muted text-muted-foreground flex size-10 items-center justify-center rounded-lg">
          <Archive className="size-5" aria-hidden="true" />
        </div>
        <div className="flex-1">
          <h2 className="text-xl font-semibold tracking-tight">Backups</h2>
          <p className="text-muted-foreground text-sm">
            Encrypted copies of the database and the TLS material, kept on this server.
          </p>
        </div>
      </div>

      <section className="bg-card text-card-foreground space-y-3 rounded-xl border p-5 shadow-xs">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="space-y-1">
            <h3 className="text-sm font-semibold">Take a backup</h3>
            <LastRun settings={settings} />
            {freeBytes !== null ? (
              <p className="text-muted-foreground text-xs">
                {formatBytes(freeBytes)} free where backups are written.
              </p>
            ) : null}
          </div>
          <div className="flex gap-2">
            <Button onClick={() => void takeBackup()} disabled={busy}>
              Back up now
            </Button>
            <Button variant="outline" onClick={() => fileInput.current?.click()} disabled={busy}>
              <Upload aria-hidden="true" />
              Upload
            </Button>
            {/* A backup from another server, restored here: the file arrives
                through the page so no shell access is needed. */}
            <input
              ref={fileInput}
              type="file"
              accept=".mgb"
              aria-label="Upload a backup file"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void upload(file);
              }}
            />
          </div>
        </div>
      </section>

      <ScheduleCard settings={settings} onSaved={(next) => setSettings(next)} />

      {loading ? (
        <Skeleton className="h-40 rounded-xl" />
      ) : isMobile ? (
        <RowCards loading={false} isEmpty={items.length === 0} empty={empty}>
          {items.map((backup) => (
            <RowCard
              key={backup.name}
              title={<span className="font-mono text-xs break-all">{backup.name}</span>}
              meta={<Badge variant="outline">{triggerLabel(backup.trigger)}</Badge>}
              facts={[
                { label: "Taken", value: whenLabel(backup.created_at) },
                { label: "Size", value: formatBytes(backup.size_bytes) },
                { label: "Version", value: backup.app_version ?? "—" },
                ...(backup.key_matches
                  ? []
                  : [{ label: "Key", value: "Made with a different secret key" }]),
              ]}
              actions={rowActions(backup)}
            />
          ))}
        </RowCards>
      ) : (
        <div className="bg-card text-card-foreground overflow-x-auto rounded-xl border shadow-xs">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Backup</TableHead>
                <TableHead>Taken</TableHead>
                <TableHead>Size</TableHead>
                <TableHead>How</TableHead>
                <TableHead>Version</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-muted-foreground py-10 text-center">
                    {empty}
                  </TableCell>
                </TableRow>
              ) : (
                items.map((backup) => (
                  <TableRow key={backup.name}>
                    <TableCell className="font-mono text-xs break-all">
                      {backup.name}
                      {backup.key_matches ? null : (
                        <span className="text-destructive block text-xs">
                          Made with a different secret key — it cannot be restored here.
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {whenLabel(backup.created_at)}
                    </TableCell>
                    <TableCell className="tabular-nums">{formatBytes(backup.size_bytes)}</TableCell>
                    <TableCell>{triggerLabel(backup.trigger)}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {backup.app_version ?? "—"}
                    </TableCell>
                    <TableCell>{rowActions(backup)}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      )}

      <Dialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
      >
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Delete this backup?</DialogTitle>
            <DialogDescription>
              {pendingDelete?.name} is removed from the server. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingDelete(null)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={() => void confirmDelete()}>
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {pendingRestore ? (
        <RestoreDialogLoader
          backup={pendingRestore}
          onClose={() => setPendingRestore(null)}
          onConfirm={() => void startRestore(pendingRestore)}
        />
      ) : null}
    </div>
  );
}

/** Placeholder seam: Task 11 replaces this with the typed-confirmation dialog. */
function RestoreDialogLoader({
  backup,
  onClose,
  onConfirm,
}: {
  backup: Backup;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Restore {backup.name}?</DialogTitle>
          <DialogDescription>
            This replaces this instance: the database and the TLS material come from the backup, and
            everyone is signed out.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={onConfirm}>
            Restore
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
