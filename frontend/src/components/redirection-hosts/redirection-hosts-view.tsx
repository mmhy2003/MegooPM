"use client";

import { toast } from "sonner";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowRightLeft, Pencil, Plus, ShieldCheck, Trash2 } from "lucide-react";

import {
  certificates,
  redirectionHosts,
  type Certificate,
  type RedirectionHost,
} from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { ConfirmDeleteDialog } from "@/components/proxy-hosts/confirm-delete-dialog";
import { RedirectionHostDialog } from "@/components/redirection-hosts/redirection-host-dialog";
import { Badge } from "@/components/ui/badge";
import { EnabledToggle } from "@/components/hosts/enabled-toggle";
import { DomainLinks } from "@/components/hosts/domain-links";
import { Button } from "@/components/ui/button";
import { SearchInput } from "@/components/ui/search-input";
import { Skeleton } from "@/components/ui/skeleton";
import { filterBySearch } from "@/lib/search";
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

export function RedirectionHostsView() {
  const [rows, setRows] = useState<RedirectionHost[]>([]);
  const [certs, setCerts] = useState<Certificate[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [dialog, setDialog] = useState<{ open: boolean; host: RedirectionHost | null }>({
    open: false,
    host: null,
  });
  const [toDelete, setToDelete] = useState<RedirectionHost | null>(null);
  const [query, setQuery] = useState("");

  const visible = useMemo(
    () => filterBySearch(rows, query, (h) => [...h.domain_names, h.forward_domain_name]),
    [rows, query],
  );

  const load = useCallback(async () => {
    try {
      const [h, c] = await Promise.all([redirectionHosts.list(), certificates.list()]);
      setRows(h);
      setCerts(c);
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

  const schemePrefix = useMemo(
    () => (scheme: RedirectionHost["forward_scheme"]) =>
      scheme === "auto" ? "" : `${scheme}://`,
    [],
  );

  /** Flip one row now, PATCH in the background, and put it back if that fails.
   *
   * Deliberately does not call refresh(): that sets loading, which would flash
   * the skeleton rows over the whole table after every toggle. */
  async function setEnabled(row: RedirectionHost, next: boolean) {
    setRows((prev) => prev.map((r) => (r.id === row.id ? { ...r, enabled: next } : r)));
    try {
      await redirectionHosts.update(row.id, { enabled: next });
    } catch (err) {
      setRows((prev) => prev.map((r) => (r.id === row.id ? { ...r, enabled: !next } : r)));
      toast.error(describeError(err).message);
    }
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <ArrowRightLeft className="size-5" />
        </div>
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Redirection Hosts</h2>
          <p className="text-sm text-muted-foreground">
            Redirect a set of domains to another domain with a chosen status code.
          </p>
        </div>
      </div>

      {loadError ? (
        <div className="flex flex-col items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
          <p className="text-sm text-destructive" role="alert">
            Couldn’t load redirection hosts: {loadError}
          </p>
          <Button variant="outline" size="sm" onClick={refresh}>
            Retry
          </Button>
        </div>
      ) : null}

      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <SearchInput
            value={query}
            onValueChange={setQuery}
            label="Search redirection hosts"
            placeholder="Domain or redirect target"
          />
          <Button size="sm" onClick={() => setDialog({ open: true, host: null })}>
            <Plus /> New redirection host
          </Button>
        </div>
        <div className="rounded-xl border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Domains</TableHead>
                <TableHead>Redirects to</TableHead>
                <TableHead>Code</TableHead>
                <TableHead>TLS</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="w-24 text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <LoadingRows cols={6} />
              ) : visible.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                    {query.trim() ? (
                      <>
                        No redirection hosts match “{query.trim()}”.{" "}
                        <Button
                          variant="link"
                          size="sm"
                          className="h-auto p-0 align-baseline"
                          onClick={() => setQuery("")}
                        >
                          Clear search
                        </Button>
                      </>
                    ) : (
                      "No redirection hosts yet. Create one to redirect domains elsewhere."
                    )}
                  </TableCell>
                </TableRow>
              ) : (
                visible.map((host) => (
                  <TableRow key={host.id}>
                    <TableCell className="font-medium">
                      <DomainLinks
                          domains={host.domain_names}
                          secure={host.certificate_id != null}
                        />
                    </TableCell>
                    <TableCell>
                      {schemePrefix(host.forward_scheme)}
                      {host.forward_domain_name}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="tabular-nums">
                        {host.forward_http_code}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {host.certificate_id != null ? (
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
                        checked={host.enabled}
                        name={host.domain_names[0]}
                        onToggle={(next) => setEnabled(host, next)}
                      />
                    </TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={`Edit ${host.domain_names[0]}`}
                          onClick={() => setDialog({ open: true, host })}
                        >
                          <Pencil />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={`Delete ${host.domain_names[0]}`}
                          onClick={() => setToDelete(host)}
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
        <RedirectionHostDialog
          key={dialog.host?.id ?? "new-redir"}
          open
          onOpenChange={(open) => !open && setDialog({ open: false, host: null })}
          host={dialog.host}
          certificates={certs}
          onSaved={refresh}
        />
      ) : null}
      {toDelete ? (
        <ConfirmDeleteDialog
          open
          onOpenChange={(open) => !open && setToDelete(null)}
          title="Delete redirection host?"
          description={`This removes ${toDelete.domain_names.join(", ")} and regenerates the nginx config.`}
          onConfirm={async () => {
            await redirectionHosts.remove(toDelete.id);
          }}
          onDeleted={refresh}
        />
      ) : null}
    </div>
  );
}
