"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Plus, RefreshCw, ShieldCheck, Trash2, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import {
  CERT_PROVIDER_LABELS,
  certificates,
  pollTask,
  type Certificate,
  type CertificateStatus,
} from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { ConfirmDeleteDialog } from "@/components/proxy-hosts/confirm-delete-dialog";
import {
  CertificateDialog,
  type PendingTask,
} from "@/components/certificates/certificate-dialog";
import { expiryInfo, formatDate, type ExpiryLevel } from "@/components/certificates/lib";
import { Badge } from "@/components/ui/badge";
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

const STATUS_META: Record<
  CertificateStatus,
  { label: string; variant: "success" | "secondary" | "destructive" | "muted" }
> = {
  active: { label: "Active", variant: "success" },
  pending: { label: "Pending", variant: "secondary" },
  failed: { label: "Failed", variant: "destructive" },
  expired: { label: "Expired", variant: "muted" },
};

function StatusBadge({ status }: { status: CertificateStatus }) {
  const meta = STATUS_META[status] ?? { label: status, variant: "muted" as const };
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}

const EXPIRY_CLASS: Record<ExpiryLevel, string> = {
  none: "text-muted-foreground",
  ok: "text-muted-foreground",
  warning: "text-amber-700 dark:text-amber-400",
  expired: "text-destructive",
};

function ExpiryCell({ cert }: { cert: Certificate }) {
  const info = expiryInfo(cert.expires_on);
  const flagged = info.level === "warning" || info.level === "expired";
  return (
    <span className={`inline-flex items-center gap-1.5 ${EXPIRY_CLASS[info.level]}`}>
      {flagged ? <TriangleAlert className="size-3.5" aria-hidden /> : null}
      <span className="tabular-nums">{formatDate(cert.expires_on)}</span>
      {info.level !== "none" ? (
        <span className="text-xs">({info.label})</span>
      ) : null}
    </span>
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

export function CertificatesView() {
  const [certs, setCerts] = useState<Certificate[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [deleteCert, setDeleteCert] = useState<Certificate | null>(null);
  const [renewingId, setRenewingId] = useState<number | null>(null);
  const [pending, setPending] = useState<PendingTask[]>([]);

  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await certificates.list();
      setCerts(list);
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

  // `load` awaits before any setState, so it never updates state synchronously
  // in the effect body; the AbortController cancels in-flight task polling on
  // unmount.
  useEffect(() => {
    const controller = new AbortController();
    abortRef.current = controller;
    void (async () => {
      await load();
    })();
    return () => controller.abort();
  }, [load]);

  // Poll an enqueued issuance/renewal task, then reconcile the list. The signal
  // aborts on unmount so a slow ACME order can't setState on a dead component.
  const trackTask = useCallback(
    async (task: PendingTask) => {
      setPending((prev) => [...prev, task]);
      try {
        const result = await pollTask(task.taskId, {
          signal: abortRef.current?.signal,
        });
        if (result.error || result.status === "FAILURE") {
          toast.error(`${task.label} failed: ${result.error ?? "unknown error"}`);
        } else if (result.ready) {
          toast.success(`${task.label} — done`);
        } else {
          toast.message(`${task.label} is still running; refresh shortly.`);
        }
      } catch (err) {
        if ((err as { name?: string }).name !== "AbortError") {
          toast.error(describeError(err).message);
        }
      } finally {
        setPending((prev) => prev.filter((t) => t.taskId !== task.taskId));
        void load();
      }
    },
    [load],
  );

  async function handleRenew(cert: Certificate) {
    setRenewingId(cert.id);
    try {
      const issued = await certificates.renew(cert.id);
      toast.success("Renewal requested");
      void trackTask({ taskId: issued.task_id, label: `Renewing “${cert.name}”` });
    } catch (err) {
      toast.error(describeError(err).message);
    } finally {
      setRenewingId(null);
    }
  }

  const warningCount = useMemo(
    () =>
      certs.filter((c) => {
        const level = expiryInfo(c.expires_on).level;
        return level === "warning" || level === "expired";
      }).length,
    [certs],
  );

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <ShieldCheck className="size-5" />
        </div>
        <div className="flex-1">
          <h2 className="text-xl font-semibold tracking-tight">Certificates</h2>
          <p className="text-sm text-muted-foreground">
            TLS certificates from Let&apos;s Encrypt or your own PEM material.
          </p>
        </div>
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          <Plus /> New certificate
        </Button>
      </div>

      {warningCount > 0 ? (
        <div className="flex items-center gap-2 rounded-xl border border-amber-500/30 bg-amber-500/5 p-3 text-sm text-amber-700 dark:text-amber-400">
          <TriangleAlert className="size-4 shrink-0" aria-hidden />
          <span>
            {warningCount} certificate{warningCount === 1 ? "" : "s"} near expiry or expired —
            renew soon to avoid an outage.
          </span>
        </div>
      ) : null}

      {pending.length > 0 ? (
        <div className="flex flex-col gap-1 rounded-xl border bg-muted/40 p-3 text-sm">
          {pending.map((t) => (
            <span key={t.taskId} className="inline-flex items-center gap-2">
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
              {t.label}…
            </span>
          ))}
        </div>
      ) : null}

      {loadError ? (
        <div className="flex flex-col items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
          <p className="text-sm text-destructive" role="alert">
            Couldn&apos;t load certificates: {loadError}
          </p>
          <Button variant="outline" size="sm" onClick={refresh}>
            Retry
          </Button>
        </div>
      ) : null}

      <div className="rounded-xl border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Domains</TableHead>
              <TableHead>Provider</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Expiry</TableHead>
              <TableHead className="w-24 text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <LoadingRows cols={6} />
            ) : certs.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                  No certificates yet. Request one from Let&apos;s Encrypt or upload your own.
                </TableCell>
              </TableRow>
            ) : (
              certs.map((cert) => {
                const canRenew = cert.provider === "letsencrypt";
                const renewing = renewingId === cert.id;
                return (
                  <TableRow key={cert.id}>
                    <TableCell className="font-medium">{cert.name}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {cert.domain_names.length ? (
                          cert.domain_names.map((d) => (
                            <span key={d} className="text-sm">
                              {d}
                            </span>
                          ))
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">
                        {CERT_PROVIDER_LABELS[cert.provider] ?? cert.provider}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={cert.status} />
                    </TableCell>
                    <TableCell>
                      <ExpiryCell cert={cert} />
                    </TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-1">
                        {canRenew ? (
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label={`Renew ${cert.name}`}
                            title="Renew"
                            disabled={renewing}
                            onClick={() => handleRenew(cert)}
                          >
                            {renewing ? (
                              <Loader2 className="animate-spin" />
                            ) : (
                              <RefreshCw />
                            )}
                          </Button>
                        ) : null}
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={`Delete ${cert.name}`}
                          title="Delete"
                          onClick={() => setDeleteCert(cert)}
                        >
                          <Trash2 />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </div>

      {createOpen ? (
        <CertificateDialog
          open
          onOpenChange={setCreateOpen}
          onSaved={(task) => {
            refresh();
            if (task) void trackTask(task);
          }}
        />
      ) : null}

      {deleteCert ? (
        <ConfirmDeleteDialog
          open
          onOpenChange={(open) => !open && setDeleteCert(null)}
          title="Delete certificate?"
          description={`This removes “${deleteCert.name}” and its on-disk key material. Hosts using it will fall back to the default certificate.`}
          onConfirm={async () => {
            await certificates.remove(deleteCert.id);
          }}
          onDeleted={refresh}
        />
      ) : null}
    </div>
  );
}
