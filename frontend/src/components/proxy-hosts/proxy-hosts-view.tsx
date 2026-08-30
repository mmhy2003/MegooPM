"use client";

import { toast } from "sonner";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Globe, ListChecks, Pencil, Plus, Server, Trash2 } from "lucide-react";

import {
  certificates,
  type Certificate,
  LB_METHOD_LABELS,
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
import { UpstreamDialog } from "@/components/upstreams/upstream-dialog";
import { Badge } from "@/components/ui/badge";
import { EnabledToggle } from "@/components/hosts/enabled-toggle";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";
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

export function ProxyHostsView() {
  const [hosts, setHosts] = useState<ProxyHost[]>([]);
  const [pools, setPools] = useState<Upstream[]>([]);
  const [lists, setLists] = useState<AccessList[]>([]);
  const [certs, setCerts] = useState<Certificate[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [hostDialog, setHostDialog] = useState<{ open: boolean; host: ProxyHost | null }>({
    open: false,
    host: null,
  });
  const [poolDialog, setPoolDialog] = useState<{ open: boolean; pool: Upstream | null }>({
    open: false,
    pool: null,
  });
  const [deleteHost, setDeleteHost] = useState<ProxyHost | null>(null);
  const [deletePool, setDeletePool] = useState<Upstream | null>(null);

  // `load` performs no synchronous setState, so it is safe to call from an
  // effect body; `refresh` (event handlers) shows the skeleton while reloading.
  const load = useCallback(async () => {
    try {
      const [h, p, a, c] = await Promise.all([
        proxyHosts.list(),
        upstreams.list(),
        accessLists.list(),
        certificates.list(),
      ]);
      setHosts(h);
      setPools(p);
      setLists(a);
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

  const poolsById = useMemo(() => {
    const map = new Map<number, Upstream>();
    for (const pool of pools) map.set(pool.id, pool);
    return map;
  }, [pools]);

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

  /** Same optimistic flip for upstream pools, which share this page. */
  async function setPoolEnabled(pool: Upstream, next: boolean) {
    setPools((prev) => prev.map((p) => (p.id === pool.id ? { ...p, enabled: next } : p)));
    try {
      await upstreams.update(pool.id, { enabled: next });
    } catch (err) {
      setPools((prev) => prev.map((p) => (p.id === pool.id ? { ...p, enabled: !next } : p)));
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
            Reverse-proxy hosts and the upstream backend pools they forward to.
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

      <Tabs defaultValue="hosts">
        <TabsList>
          <TabsTab value="hosts">
            <Globe /> Hosts
          </TabsTab>
          <TabsTab value="pools">
            <Server /> Upstream pools
          </TabsTab>
        </TabsList>

        {/* ---- Hosts ---- */}
        <TabsPanel value="hosts" className="space-y-3">
          <div className="flex justify-end">
            <Button size="sm" onClick={() => setHostDialog({ open: true, host: null })}>
              <Plus /> New proxy host
            </Button>
          </div>
          <div className="rounded-xl border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Domains</TableHead>
                  <TableHead>Upstream</TableHead>
                  <TableHead>Access list</TableHead>
                  <TableHead>Scheme</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="w-24 text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading ? (
                  <LoadingRows cols={6} />
                ) : hosts.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                      No proxy hosts yet. Create a pool, then add a host that forwards to it.
                    </TableCell>
                  </TableRow>
                ) : (
                  hosts.map((host) => {
                    const pool = poolsById.get(host.upstream_id);
                    const list =
                      host.access_list_id != null
                        ? listsById.get(host.access_list_id)
                        : null;
                    return (
                      <TableRow key={host.id}>
                        <TableCell className="font-medium">
                          <div className="flex flex-wrap gap-1">
                            {host.domain_names.map((d) => (
                              <span key={d}>{d}</span>
                            ))}
                          </div>
                        </TableCell>
                        <TableCell>
                          {pool ? (
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
                      />
                        </TableCell>
                        <TableCell>
                          <div className="flex justify-end gap-1">
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
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })
                )}
              </TableBody>
            </Table>
          </div>
        </TabsPanel>

        {/* ---- Pools ---- */}
        <TabsPanel value="pools" className="space-y-3">
          <div className="flex justify-end">
            <Button size="sm" onClick={() => setPoolDialog({ open: true, pool: null })}>
              <Plus /> New upstream pool
            </Button>
          </div>
          <div className="rounded-xl border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>LB method</TableHead>
                  <TableHead>Backends</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="w-24 text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading ? (
                  <LoadingRows cols={5} />
                ) : pools.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5} className="py-10 text-center text-muted-foreground">
                      No upstream pools yet. Create one to define a load-balanced backend set.
                    </TableCell>
                  </TableRow>
                ) : (
                  pools.map((pool) => {
                    const backends = pool.backends ?? [];
                    const up = backends.filter((b) => (b.enabled ?? true) && !(b.down ?? false)).length;
                    return (
                      <TableRow key={pool.id}>
                        <TableCell className="font-medium">
                          {pool.name}
                          {pool.description ? (
                            <span className="block text-xs font-normal text-muted-foreground">
                              {pool.description}
                            </span>
                          ) : null}
                        </TableCell>
                        <TableCell>
                          <Badge variant="secondary">{LB_METHOD_LABELS[pool.lb_method]}</Badge>
                        </TableCell>
                        <TableCell>
                          <span className="tabular-nums">
                            {up}/{backends.length} up
                          </span>
                        </TableCell>
                        <TableCell>
                          <EnabledToggle
                            checked={pool.enabled ?? true}
                            name={pool.name}
                            onToggle={(next) => setPoolEnabled(pool, next)}
                          />
                        </TableCell>
                        <TableCell>
                          <div className="flex justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              aria-label={`Edit ${pool.name}`}
                              onClick={() => setPoolDialog({ open: true, pool })}
                            >
                              <Pencil />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              aria-label={`Delete ${pool.name}`}
                              onClick={() => setDeletePool(pool)}
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
        </TabsPanel>
      </Tabs>

      {hostDialog.open ? (
        <ProxyHostDialog
          key={hostDialog.host?.id ?? "new-host"}
          open
          onOpenChange={(open) => !open && setHostDialog({ open: false, host: null })}
          host={hostDialog.host}
          pools={pools}
          lists={lists}
          certs={certs}
          onSaved={refresh}
        />
      ) : null}
      {poolDialog.open ? (
        <UpstreamDialog
          key={poolDialog.pool?.id ?? "new-pool"}
          open
          onOpenChange={(open) => !open && setPoolDialog({ open: false, pool: null })}
          upstream={poolDialog.pool}
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
      {deletePool ? (
        <ConfirmDeleteDialog
          open
          onOpenChange={(open) => !open && setDeletePool(null)}
          title="Delete upstream pool?"
          description={`This deletes ${deletePool.name} and its backends. Pools still referenced by a host cannot be deleted.`}
          onConfirm={async () => {
            await upstreams.remove(deletePool.id);
          }}
          onDeleted={refresh}
        />
      ) : null}
    </div>
  );
}
