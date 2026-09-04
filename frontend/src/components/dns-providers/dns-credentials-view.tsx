"use client";

import { useCallback, useEffect, useState } from "react";
import { Pencil, Plus, ShieldCheck, Trash2 } from "lucide-react";

import {
  dnsCredentials,
  dnsProviders,
  type DnsCredential,
  type DnsProviderInfo,
} from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { ConfirmDeleteDialog } from "@/components/proxy-hosts/confirm-delete-dialog";
import { DnsCredentialDialog } from "@/components/dns-providers/dns-credential-dialog";
import { VerifyCredentialDialog } from "@/components/dns-providers/verify-credential-dialog";
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

export function DnsCredentialsView() {
  const [rows, setRows] = useState<DnsCredential[]>([]);
  const [catalog, setCatalog] = useState<DnsProviderInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [dialog, setDialog] = useState<{ open: boolean; credential: DnsCredential | null }>({
    open: false,
    credential: null,
  });
  const [verifyTarget, setVerifyTarget] = useState<DnsCredential | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DnsCredential | null>(null);

  // `load` awaits before any setState, so it is safe to call from the effect.
  const load = useCallback(async () => {
    try {
      const [list, providers] = await Promise.all([dnsCredentials.list(), dnsProviders.catalog()]);
      setRows(list);
      setCatalog(providers);
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

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Saved provider credentials for DNS-01 challenges (wildcards and hosts not reachable on
        port 80). Secrets are encrypted at rest and never shown again.
      </p>

      {loadError ? (
        <div className="flex flex-col items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
          <p className="text-sm text-destructive" role="alert">
            Couldn’t load DNS credentials: {loadError}
          </p>
          <Button variant="outline" size="sm" onClick={refresh}>
            Retry
          </Button>
        </div>
      ) : null}

      <div className="flex justify-end">
        <Button
          size="sm"
          onClick={() => setDialog({ open: true, credential: null })}
          disabled={catalog.length === 0}
        >
          <Plus /> New credentials
        </Button>
      </div>
      <div className="bg-card text-card-foreground rounded-xl border shadow-xs">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Provider</TableHead>
              <TableHead>Credentials set</TableHead>
              <TableHead>Used by</TableHead>
              <TableHead className="w-32 text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <LoadingRows cols={5} />
            ) : rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="py-10 text-center text-muted-foreground">
                  No DNS credentials yet. Add one to request DNS-01 (wildcard) certificates.
                </TableCell>
              </TableRow>
            ) : (
              rows.map((c) => (
                <TableRow key={c.id}>
                  <TableCell className="font-medium">{c.name}</TableCell>
                  <TableCell>
                    <Badge variant="outline">{c.provider_label}</Badge>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {c.secret_fields.map((f) => (
                        <Badge key={f} variant="muted" className="font-mono text-xs">
                          {f}
                        </Badge>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell>
                    <span title={c.in_use_by.map((x) => x.name).join(", ")}>
                      {c.in_use_by.length}
                    </span>
                  </TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Edit ${c.name}`}
                        onClick={() => setDialog({ open: true, credential: c })}
                      >
                        <Pencil />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Verify ${c.name}`}
                        onClick={() => setVerifyTarget(c)}
                      >
                        <ShieldCheck />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Delete ${c.name}`}
                        onClick={() => setDeleteTarget(c)}
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

      {/* `key` remounts each dialog per target so its form starts fresh. */}
      <DnsCredentialDialog
        key={dialog.credential?.id ?? "new-credential"}
        open={dialog.open}
        onOpenChange={(open) => setDialog((s) => ({ ...s, open }))}
        credential={dialog.credential}
        catalog={catalog}
        onSaved={refresh}
      />
      <VerifyCredentialDialog
        key={verifyTarget?.id ?? "no-verify"}
        open={verifyTarget !== null}
        onOpenChange={(open) => {
          if (!open) setVerifyTarget(null);
        }}
        credential={verifyTarget}
      />
      <ConfirmDeleteDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        title="Delete DNS credentials"
        description={
          deleteTarget
            ? `Delete “${deleteTarget.name}”? Certificates that still use it must be deleted or re-pointed first.`
            : ""
        }
        onConfirm={async () => {
          if (deleteTarget) await dnsCredentials.remove(deleteTarget.id);
        }}
        onDeleted={refresh}
      />
    </div>
  );
}
