"use client";

import { useCallback, useEffect, useState } from "react";
import { KeyRound, ListChecks, Network, Pencil, Plus, Trash2 } from "lucide-react";

import { accessLists, type AccessList } from "@/lib/api";
import { describeError, satisfyLabel } from "@/components/access-lists/lib";
import { AccessListDialog } from "@/components/access-lists/access-list-dialog";
import { AccessListEditor } from "@/components/access-lists/access-list-editor";
import { ConfirmDeleteDialog } from "@/components/proxy-hosts/confirm-delete-dialog";
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

export function AccessListsView() {
  const [lists, setLists] = useState<AccessList[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [deleteList, setDeleteList] = useState<AccessList | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await accessLists.list();
      setLists(data);
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
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <ListChecks className="size-5" />
        </div>
        <div className="flex-1">
          <h2 className="text-xl font-semibold tracking-tight">Access Lists</h2>
          <p className="text-sm text-muted-foreground">
            Basic-auth users and allow/deny IP rules you can attach to a proxy
            host.
          </p>
        </div>
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          <Plus /> New access list
        </Button>
      </div>

      {loadError ? (
        <div className="flex flex-col items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
          <p className="text-sm text-destructive" role="alert">
            Couldn&apos;t load access lists: {loadError}
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
              <TableHead>Satisfy</TableHead>
              <TableHead>Pass auth</TableHead>
              <TableHead>Users</TableHead>
              <TableHead>IP rules</TableHead>
              <TableHead className="w-24 text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <LoadingRows cols={6} />
            ) : lists.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={6}
                  className="py-10 text-center text-muted-foreground"
                >
                  No access lists yet. Create one, then attach it to a proxy host
                  to gate access.
                </TableCell>
              </TableRow>
            ) : (
              lists.map((list) => (
                <TableRow key={list.id}>
                  <TableCell className="font-medium">{list.name}</TableCell>
                  <TableCell>
                    <Badge variant="secondary">
                      {satisfyLabel(list.satisfy_any ?? false)}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Badge variant={list.pass_auth ? "outline" : "muted"}>
                      {list.pass_auth ? "On" : "Off"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <span className="inline-flex items-center gap-1.5 tabular-nums">
                      <KeyRound className="size-3.5 text-muted-foreground" />
                      {list.auth_users?.length ?? 0}
                    </span>
                  </TableCell>
                  <TableCell>
                    <span className="inline-flex items-center gap-1.5 tabular-nums">
                      <Network className="size-3.5 text-muted-foreground" />
                      {list.client_rules?.length ?? 0}
                    </span>
                  </TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Edit ${list.name}`}
                        onClick={() => setEditId(list.id)}
                      >
                        <Pencil />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Delete ${list.name}`}
                        onClick={() => setDeleteList(list)}
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

      {createOpen ? (
        <AccessListDialog
          open
          onOpenChange={setCreateOpen}
          onCreated={(list) => {
            refresh();
            // Jump straight into the editor to add users / IP rules.
            setEditId(list.id);
          }}
        />
      ) : null}

      {editId !== null ? (
        <AccessListEditor
          key={editId}
          open
          onOpenChange={(open) => !open && setEditId(null)}
          listId={editId}
          onChanged={refresh}
        />
      ) : null}

      {deleteList ? (
        <ConfirmDeleteDialog
          open
          onOpenChange={(open) => !open && setDeleteList(null)}
          title="Delete access list?"
          description={`This removes “${deleteList.name}” and its users and IP rules. Hosts using it will no longer be gated.`}
          onConfirm={async () => {
            await accessLists.remove(deleteList.id);
          }}
          onDeleted={refresh}
        />
      ) : null}
    </div>
  );
}
