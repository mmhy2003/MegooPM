"use client";

import { toast } from "sonner";

import { useCallback, useEffect, useState } from "react";
import { Network, Pencil, Plus, ShieldCheck, Trash2 } from "lucide-react";

import {
  certificates,
  streams,
  upstreams,
  type Certificate,
  type Stream,
  type Upstream,
} from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { ConfirmDeleteDialog } from "@/components/proxy-hosts/confirm-delete-dialog";
import { StreamDialog } from "@/components/streams/stream-dialog";
import { Badge } from "@/components/ui/badge";
import { EnabledToggle } from "@/components/hosts/enabled-toggle";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function ProtocolBadges({ tcp, udp }: { tcp: boolean; udp: boolean }) {
  return (
    <div className="flex gap-1">
      {tcp ? <Badge variant="secondary">TCP</Badge> : null}
      {udp ? <Badge variant="secondary">UDP</Badge> : null}
    </div>
  );
}

function LoadingRows({ cols }: { cols: number }) {
  return (
    <>
      {[0, 1, 2].map((i) => (
        <TableRow key={i}>
          {Array.from({ length: cols }).map((_, c) => (
            <TableCell key={c}>
              <Skeleton className="h-4 w-full" />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  );
}

export function StreamsView() {
  const [rows, setRows] = useState<Stream[]>([]);
  const [certs, setCerts] = useState<Certificate[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [dialog, setDialog] = useState<{ open: boolean; stream: Stream | null }>({
    open: false,
    stream: null,
  });
  const [toDelete, setToDelete] = useState<Stream | null>(null);
  const [pools, setPools] = useState<Upstream[]>([]);

  const load = useCallback(async () => {
    try {
      const [s, c, p] = await Promise.all([
        streams.list(),
        certificates.list(),
        upstreams.list(),
      ]);
      setRows(s);
      setCerts(c);
      setPools(p);
      setLoadError(null);
    } catch (err) {
      setLoadError(describeError(err).message);
    } finally {
      setLoading(false);
    }
  }, []);

  const refresh = useCallback(() => {
    setLoading(true);
    void load();
  }, [load]);

  useEffect(() => {
    let active = true;
    void (async () => {
      if (active) await load();
    })();
    return () => {
      active = false;
    };
  }, [load]);

  /** Flip one row now, PATCH in the background, and put it back if that fails.
   *
   * Deliberately does not call refresh(): that sets loading, which would flash
   * the skeleton rows over the whole table after every toggle. */
  async function setEnabled(row: Stream, next: boolean) {
    setRows((prev) => prev.map((r) => (r.id === row.id ? { ...r, enabled: next } : r)));
    try {
      await streams.update(row.id, { enabled: next });
    } catch (err) {
      setRows((prev) => prev.map((r) => (r.id === row.id ? { ...r, enabled: !next } : r)));
      toast.error(describeError(err).message);
    }
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <Network className="size-5" />
        </div>
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Streams</h2>
          <p className="text-sm text-muted-foreground">
            Raw TCP/UDP port forwarding to a backend host.
          </p>
        </div>
      </div>

      {loadError ? (
        <div className="flex flex-col items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
          <p className="text-sm text-destructive" role="alert">
            Couldn’t load streams: {loadError}
          </p>
          <Button variant="outline" size="sm" onClick={refresh}>
            Retry
          </Button>
        </div>
      ) : null}

      <div className="space-y-3">
        <div className="flex justify-end">
          <Button size="sm" onClick={() => setDialog({ open: true, stream: null })}>
            <Plus /> New stream
          </Button>
        </div>
        <div className="rounded-xl border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Incoming port</TableHead>
                <TableHead>Forward to</TableHead>
                <TableHead>Protocols</TableHead>
                <TableHead>TLS</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="w-24 text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <LoadingRows cols={6} />
              ) : rows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                    No streams yet. Create one to forward a TCP/UDP port to a backend.
                  </TableCell>
                </TableRow>
              ) : (
                rows.map((stream) => (
                  <TableRow key={stream.id}>
                    <TableCell className="font-medium tabular-nums">
                      {stream.incoming_port}
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {stream.forward_host}:{stream.forward_port}
                    </TableCell>
                    <TableCell>
                      <ProtocolBadges tcp={stream.tcp_forwarding} udp={stream.udp_forwarding} />
                    </TableCell>
                    <TableCell>
                      {stream.certificate_id != null ? (
                        <span className="inline-flex items-center gap-1.5">
                          <ShieldCheck className="size-3.5 text-muted-foreground" />
                          TLS
                        </span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <EnabledToggle
                        checked={stream.enabled}
                        name={String(stream.incoming_port)}
                        onToggle={(next) => setEnabled(stream, next)}
                      />
                    </TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={`Edit stream on port ${stream.incoming_port}`}
                          onClick={() => setDialog({ open: true, stream })}
                        >
                          <Pencil />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={`Delete stream on port ${stream.incoming_port}`}
                          onClick={() => setToDelete(stream)}
                        >
                          <Trash2 />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </div>

      {dialog.open ? (
        <StreamDialog
          key={dialog.stream?.id ?? "new-stream"}
          open
          onOpenChange={(open) => !open && setDialog({ open: false, stream: null })}
          stream={dialog.stream}
          certificates={certs}
          pools={pools}
          onSaved={refresh}
        />
      ) : null}
      {toDelete ? (
        <ConfirmDeleteDialog
          open
          onOpenChange={(open) => !open && setToDelete(null)}
          title="Delete stream?"
          description={`This removes the stream on port ${toDelete.incoming_port} and regenerates the nginx config.`}
          onConfirm={async () => {
            await streams.remove(toDelete.id);
          }}
          onDeleted={refresh}
        />
      ) : null}
    </div>
  );
}
