"use client";

import { toast } from "sonner";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Globe, ListChecks, Pencil, Plus, Server, Trash2 } from "lucide-react";

import {
  customPages,
  type CustomPageSummary,
  certificates,
  type Certificate,
  accessLists,
  proxyHosts,
  upstreams,
  type AccessList,
  type ProxyHost,
  type Upstream,
} from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { ConfirmDeleteDialog } from "@/components/proxy-hosts/confirm-delete-dialog";
import { ProxyHostDialog } from "@/components/proxy-hosts/proxy-host-dialog";
import { Badge } from "@/components/ui/badge";
import { EnabledToggle } from "@/components/hosts/enabled-toggle";
import { DomainLinks } from "@/components/hosts/domain-links";
import { Button } from "@/components/ui/button";
import { useCanWrite } from "@/lib/auth/can-write";
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
import { RowCard, RowCards } from "@/components/ui/row-cards";
import { useIsMobile } from "@/hooks/use-mobile";

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

export function ProxyHostsView() {
  const [hosts, setHosts] = useState<ProxyHost[]>([]);
  const canWrite = useCanWrite();
  const [pools, setPools] = useState<Upstream[]>([]);
  const [lists, setLists] = useState<AccessList[]>([]);
  const [certs, setCerts] = useState<Certificate[]>([]);
  // Only for the "custom page" location target; the list is small and static.
  const [pages, setPages] = useState<CustomPageSummary[]>([]);
  const [loading, setLoading] = useState(true);
  // Below the breakpoint the table becomes cards: see RowCards.
  const isMobile = useIsMobile();
  const [loadError, setLoadError] = useState<string | null>(null);

  const [hostDialog, setHostDialog] = useState<{ open: boolean; host: ProxyHost | null }>({
    open: false,
    host: null,
  });
  const [deleteHost, setDeleteHost] = useState<ProxyHost | null>(null);
  const [query, setQuery] = useState("");

  // `load` performs no synchronous setState, so it is safe to call from an
  // effect body; `refresh` (event handlers) shows the skeleton while reloading.
  const load = useCallback(async () => {
    try {
      const [h, p, a, c, pg] = await Promise.all([
        proxyHosts.list(),
        upstreams.list(),
        accessLists.list(),
        certificates.list(),
        customPages.list(),
      ]);
      setHosts(h);
      setPools(p);
      setLists(a);
      setCerts(c);
      setPages(pg);
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

  const poolsById = useMemo(() => {
    const map = new Map<number, Upstream>();
    for (const pool of pools) map.set(pool.id, pool);
    return map;
  }, [pools]);

  // Domains and the forward target: what an operator remembers about a host.
  // Not the status or scheme columns — matching those makes `active` and `http`
  // return half the table.
  const visible = useMemo(
    () => filterBySearch(hosts, query, (h) => [...h.domain_names, h.forward_host]),
    [hosts, query],
  );

  const listsById = useMemo(() => {
    const map = new Map<number, AccessList>();
    for (const list of lists) map.set(list.id, list);
    return map;
  }, [lists]);

  /** Flip one row now, PATCH in the background, and put it back if that fails.
   *
   * Deliberately does not call refresh(): that sets loading, which would flash
   * the skeleton rows over the whole table after every toggle. */
  async function setEnabled(row: ProxyHost, next: boolean) {
    setHosts((prev) => prev.map((r) => (r.id === row.id ? { ...r, enabled: next } : r)));
    try {
      await proxyHosts.update(row.id, { enabled: next });
    } catch (err) {
      setHosts((prev) => prev.map((r) => (r.id === row.id ? { ...r, enabled: !next } : r)));
      toast.error(describeError(err).message);
    }
  }

  /** Same optimistic flip as setEnabled, for the other switch on the row.
   *
   * On the list rather than only in the dialog because planned downtime starts
   * and ends at a moment someone is watching a deploy, and the allow-list that
   * makes it usable is set once, in the dialog, long before that moment. */
  async function setMaintenance(row: ProxyHost, next: boolean) {
    setHosts((prev) =>
      prev.map((r) => (r.id === row.id ? { ...r, maintenance_enabled: next } : r)),
    );
    try {
      await proxyHosts.update(row.id, { maintenance_enabled: next });
    } catch (err) {
      setHosts((prev) =>
        prev.map((r) => (r.id === row.id ? { ...r, maintenance_enabled: !next } : r)),
      );
      toast.error(describeError(err).message);
    }
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <Globe className="size-5" />
        </div>
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Proxy Hosts</h2>
          <p className="text-sm text-muted-foreground">
            Reverse-proxy hosts forwarding traffic to upstream pools.
          </p>
        </div>
      </div>

      {loadError ? (
        <div className="flex flex-col items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
          <p className="text-sm text-destructive" role="alert">
            Couldn’t load data: {loadError}
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
            label="Search proxy hosts"
            placeholder="Domain or forward host"
          />
          {canWrite ? (
            <Button size="sm" onClick={() => setHostDialog({ open: true, host: null })}>
              <Plus /> New proxy host
            </Button>
          ) : (
            <p className="text-muted-foreground text-sm">
              Read-only — ask an admin to make changes.
            </p>
          )}
        </div>
        {isMobile ? (
          <RowCards
            loading={loading}
            isEmpty={visible.length === 0}
            empty={
              query.trim() ? (
                <>
                  No proxy hosts match “{query.trim()}”.{" "}
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
                "No proxy hosts yet. Create a pool, then add a host that forwards to it."
              )
            }
          >
            {visible.map((host) => {
              const pool = host.upstream_id != null ? poolsById.get(host.upstream_id) : undefined;
              const list = host.access_list_id != null ? listsById.get(host.access_list_id) : null;
              return (
                <RowCard
                  key={host.id}
                  title={
                    <>
                      <DomainLinks
                        domains={host.domain_names}
                        secure={host.certificate_id != null}
                      />
                      {host.maintenance_enabled ? (
                        <Badge
                          variant="outline"
                          className="mt-1 border-amber-500/40 text-amber-600 dark:text-amber-400"
                        >
                          Maintenance
                        </Badge>
                      ) : null}
                    </>
                  }
                  meta={
                    <EnabledToggle
                      checked={host.enabled ?? true}
                      name={host.domain_names[0]}
                      onToggle={(next) => setEnabled(host, next)}
                      disabled={!canWrite}
                    />
                  }
                  facts={[
                    {
                      label: "Upstream",
                      value:
                        host.upstream_id == null ? (
                          <span className="font-mono text-xs">
                            {host.forward_host}:{host.forward_port}
                          </span>
                        ) : pool ? (
                          <span className="inline-flex items-center gap-1.5">
                            <Server className="size-3.5 text-muted-foreground" />
                            {pool.name}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">#{host.upstream_id}</span>
                        ),
                    },
                    {
                      label: "Access list",
                      value:
                        host.access_list_id != null ? (
                          <span className="inline-flex items-center gap-1.5">
                            <ListChecks className="size-3.5 text-muted-foreground" />
                            {list ? list.name : `#${host.access_list_id}`}
                          </span>
                        ) : null,
                    },
                    {
                      label: "Scheme",
                      value: <Badge variant="outline">{host.forward_scheme}</Badge>,
                    },
                    {
                      label: "Maintenance",
                      value: (
                        <EnabledToggle
                          checked={host.maintenance_enabled ?? false}
                          name={host.domain_names[0]}
                          label={`Maintenance for ${host.domain_names[0]}`}
                          onToggle={(next) => setMaintenance(host, next)}
                          disabled={!canWrite}
                        />
                      ),
                    },
                  ]}
                  actions={
                    canWrite ? (
                      <>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={`Edit ${host.domain_names[0]}`}
                          onClick={() => setHostDialog({ open: true, host })}
                        >
                          <Pencil />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={`Delete ${host.domain_names[0]}`}
                          onClick={() => setDeleteHost(host)}
                        >
                          <Trash2 />
                        </Button>
                      </>
                    ) : null
                  }
                />
              );
            })}
          </RowCards>
        ) : (
          <div className="bg-card text-card-foreground rounded-xl border shadow-xs">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Domains</TableHead>
                  <TableHead>Upstream</TableHead>
                  <TableHead>Access list</TableHead>
                  <TableHead>Scheme</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Maintenance</TableHead>
                  <TableHead className="w-24 text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading ? (
                  <LoadingRows cols={7} />
                ) : visible.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={7} className="py-10 text-center text-muted-foreground">
                      {query.trim() ? (
                        <>
                          No proxy hosts match “{query.trim()}”.{" "}
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
                        "No proxy hosts yet. Create a pool, then add a host that forwards to it."
                      )}
                    </TableCell>
                  </TableRow>
                ) : (
                  visible.map((host) => {
                    const pool =
                      host.upstream_id != null ? poolsById.get(host.upstream_id) : undefined;
                    const list =
                      host.access_list_id != null ? listsById.get(host.access_list_id) : null;
                    return (
                      <TableRow key={host.id}>
                        <TableCell className="font-medium">
                          <DomainLinks
                            domains={host.domain_names}
                            secure={host.certificate_id != null}
                          />
                          {host.maintenance_enabled ? (
                            // A host left under maintenance looks exactly like a
                            // host that is down, and the only other signal for it
                            // is inside the edit dialog.
                            <Badge
                              variant="outline"
                              className="mt-1 border-amber-500/40 text-amber-600 dark:text-amber-400"
                            >
                              Maintenance
                            </Badge>
                          ) : null}
                        </TableCell>
                        <TableCell>
                          {host.upstream_id == null ? (
                            // A host-targeted row has no pool to name.
                            <span className="font-mono text-xs">
                              {host.forward_host}:{host.forward_port}
                            </span>
                          ) : pool ? (
                            <span className="inline-flex items-center gap-1.5">
                              <Server className="size-3.5 text-muted-foreground" />
                              {pool.name}
                            </span>
                          ) : (
                            <span className="text-muted-foreground">#{host.upstream_id}</span>
                          )}
                        </TableCell>
                        <TableCell>
                          {host.access_list_id != null ? (
                            <span className="inline-flex items-center gap-1.5">
                              <ListChecks className="size-3.5 text-muted-foreground" />
                              {list ? list.name : `#${host.access_list_id}`}
                            </span>
                          ) : (
                            <span className="text-muted-foreground">—</span>
                          )}
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline">{host.forward_scheme}</Badge>
                        </TableCell>
                        <TableCell>
                          <EnabledToggle
                            checked={host.enabled ?? true}
                            name={host.domain_names[0]}
                            onToggle={(next) => setEnabled(host, next)}
                            disabled={!canWrite}
                          />
                        </TableCell>
                        <TableCell>
                          <EnabledToggle
                            checked={host.maintenance_enabled ?? false}
                            name={host.domain_names[0]}
                            label={`Maintenance for ${host.domain_names[0]}`}
                            onToggle={(next) => setMaintenance(host, next)}
                            disabled={!canWrite}
                          />
                        </TableCell>
                        <TableCell>
                          <div className="flex justify-end gap-1">
                            {canWrite ? (
                              <>
                                <Button
                                  variant="ghost"
                                  size="icon-sm"
                                  aria-label={`Edit ${host.domain_names[0]}`}
                                  onClick={() => setHostDialog({ open: true, host })}
                                >
                                  <Pencil />
                                </Button>
                                <Button
                                  variant="ghost"
                                  size="icon-sm"
                                  aria-label={`Delete ${host.domain_names[0]}`}
                                  onClick={() => setDeleteHost(host)}
                                >
                                  <Trash2 />
                                </Button>
                              </>
                            ) : null}
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })
                )}
              </TableBody>
            </Table>
          </div>
        )}
      </div>

      {hostDialog.open ? (
        <ProxyHostDialog
          key={hostDialog.host?.id ?? "new-host"}
          open
          onOpenChange={(open) => !open && setHostDialog({ open: false, host: null })}
          host={hostDialog.host}
          pools={pools}
          pages={pages}
          lists={lists}
          certs={certs}
          onSaved={refresh}
        />
      ) : null}
      {deleteHost ? (
        <ConfirmDeleteDialog
          open
          onOpenChange={(open) => !open && setDeleteHost(null)}
          title="Delete proxy host?"
          description={`This removes ${deleteHost.domain_names.join(", ")} and regenerates the nginx config.`}
          onConfirm={async () => {
            await proxyHosts.remove(deleteHost.id);
          }}
          onDeleted={refresh}
        />
      ) : null}
    </div>
  );
}
