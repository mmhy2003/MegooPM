"use client";

import { toast } from "sonner";

import { useCallback, useEffect, useState } from "react";
import { Pencil, Plus, Server, Trash2 } from "lucide-react";

import {
  LB_METHOD_LABELS,
  upstreams,
  type Upstream,
  type UpstreamContext,
} from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { ConfirmDeleteDialog } from "@/components/proxy-hosts/confirm-delete-dialog";
import { UpstreamDialog } from "@/components/upstreams/upstream-dialog";
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

/** Table-width context names.
 *
 * Deliberately not the dialog's CONTEXT_LABELS: those are written to teach
 * someone choosing a value ("HTTP only (proxy hosts)") and are far too long to
 * scan down a column. Two maps for two jobs beats one that does neither well.
 */
const CONTEXT_SHORT_LABELS: Record<UpstreamContext, string> = {
  http: "HTTP",
  stream: "Streams",
  both: "Both",
};

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

export function UpstreamsView() {
  const [pools, setPools] = useState<Upstream[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [poolDialog, setPoolDialog] = useState<{ open: boolean; pool: Upstream | null }>({
    open: false,
    pool: null,
  });
  const [deletePool, setDeletePool] = useState<Upstream | null>(null);

  // `load` performs no synchronous setState, so it is safe to call from an
  // effect body; `refresh` (event handlers) shows the skeleton while reloading.
  const load = useCallback(async () => {
    try {
      setPools(await upstreams.list());
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
          <Server className="size-5" />
        </div>
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Upstream Pools</h2>
          <p className="text-sm text-muted-foreground">
            Backend server pools that proxy hosts and streams forward to.
          </p>
        </div>
      </div>

      {loadError ? (
        <div className="flex flex-col items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
          <p className="text-sm text-destructive" role="alert">
            Couldn’t load upstream pools: {loadError}
          </p>
          <Button variant="outline" size="sm" onClick={refresh}>
            Retry
          </Button>
        </div>
      ) : null}

      <div className="space-y-3">
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
                <TableHead>Context</TableHead>
                <TableHead>Backends</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="w-24 text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <LoadingRows cols={6} />
              ) : pools.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                    No upstream pools yet. Create one to define a load-balanced backend set.
                  </TableCell>
                </TableRow>
              ) : (
                pools.map((pool) => {
                  const backends = pool.backends ?? [];
                  const up = backends.filter(
                    (b) => (b.enabled ?? true) && !(b.down ?? false),
                  ).length;
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
                        {/* outline, not secondary: a different kind of fact
                            from the method badge sitting beside it. */}
                        <Badge variant="outline">
                          {CONTEXT_SHORT_LABELS[pool.context]}
                        </Badge>
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
      </div>

      {poolDialog.open ? (
        <UpstreamDialog
          key={poolDialog.pool?.id ?? "new-pool"}
          open
          onOpenChange={(open) => !open && setPoolDialog({ open: false, pool: null })}
          upstream={poolDialog.pool}
          onSaved={refresh}
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
