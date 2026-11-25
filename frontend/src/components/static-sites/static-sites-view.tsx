"use client";

import { toast } from "sonner";

import { useCallback, useEffect, useMemo, useState } from "react";
import { FolderOpen, Lock, Pencil, Plus, ShieldCheck, Trash2, TriangleAlert } from "lucide-react";

import {
  accessLists,
  certificates,
  staticSites,
  type AccessList,
  type Certificate,
  type StaticSite,
  type StaticSiteFolders,
} from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { ConfirmDeleteDialog } from "@/components/proxy-hosts/confirm-delete-dialog";
import { StaticSiteDialog } from "@/components/static-sites/static-site-dialog";
import { FOLDER_STATUS_TEXT } from "@/components/static-sites/lib";
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

/** Until the folder scan answers (or if it fails), assume nothing about the
 *  mount: the not-mounted notice is only shown when the API says so. */
const UNKNOWN_FOLDERS: StaticSiteFolders = { available: true, folders: [], reason: null };

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

/** The folder, with a warning badge when nginx will not be able to serve it. */
function FolderCell({ site }: { site: StaticSite }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <code className="font-mono text-xs">{site.folder}</code>
      {site.folder_status !== "ok" ? (
        <Badge variant="warning" className="gap-1">
          <TriangleAlert className="size-3" />
          {FOLDER_STATUS_TEXT[site.folder_status]}
        </Badge>
      ) : null}
    </span>
  );
}

export function StaticSitesView() {
  const [rows, setRows] = useState<StaticSite[]>([]);
  const canWrite = useCanWrite();
  const [certs, setCerts] = useState<Certificate[]>([]);
  const [lists, setLists] = useState<AccessList[]>([]);
  const [folders, setFolders] = useState<StaticSiteFolders>(UNKNOWN_FOLDERS);
  const [loading, setLoading] = useState(true);
  // Below the breakpoint the table becomes cards: see RowCards.
  const isMobile = useIsMobile();
  const [loadError, setLoadError] = useState<string | null>(null);

  const [dialog, setDialog] = useState<{ open: boolean; site: StaticSite | null }>({
    open: false,
    site: null,
  });
  const [toDelete, setToDelete] = useState<StaticSite | null>(null);
  const [query, setQuery] = useState("");

  const listsById = useMemo(() => new Map(lists.map((l) => [l.id, l])), [lists]);

  const visible = useMemo(
    () => filterBySearch(rows, query, (s) => [...s.domain_names, s.folder]),
    [rows, query],
  );

  const load = useCallback(async () => {
    try {
      const [s, c, l, f] = await Promise.all([
        staticSites.list(),
        certificates.list(),
        accessLists.list(),
        // The picker is a convenience: a failed scan leaves typing a path.
        staticSites.folders().catch(() => UNKNOWN_FOLDERS),
      ]);
      setRows(s);
      setCerts(c);
      setLists(l);
      setFolders(f);
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
  async function setEnabled(row: StaticSite, next: boolean) {
    setRows((prev) => prev.map((r) => (r.id === row.id ? { ...r, enabled: next } : r)));
    try {
      await staticSites.update(row.id, { enabled: next });
    } catch (err) {
      setRows((prev) => prev.map((r) => (r.id === row.id ? { ...r, enabled: !next } : r)));
      toast.error(describeError(err).message);
    }
  }

  function accessLabel(site: StaticSite) {
    if (site.access_list_id == null) return <span className="text-muted-foreground">Public</span>;
    const list = listsById.get(site.access_list_id);
    return (
      <span className="inline-flex items-center gap-1.5">
        <Lock className="size-3.5 text-muted-foreground" />
        {list ? list.name : `#${site.access_list_id}`}
      </span>
    );
  }

  const tls = (site: StaticSite) =>
    site.certificate_id != null ? (
      <span className="inline-flex items-center gap-1.5">
        <ShieldCheck className="size-3.5 text-muted-foreground" />
        TLS
      </span>
    ) : null;

  const emptyMessage = query.trim() ? (
    <>
      No static sites match “{query.trim()}”.{" "}
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
    "No static sites yet. Create one to serve a folder of files."
  );

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <FolderOpen className="size-5" />
        </div>
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Static Sites</h2>
          <p className="text-sm text-muted-foreground">
            Serve the plain files in a folder for a set of domains, with no app behind them.
          </p>
        </div>
      </div>

      {loadError ? (
        <div className="flex flex-col items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
          <p className="text-sm text-destructive" role="alert">
            Couldn’t load static sites: {loadError}
          </p>
          <Button variant="outline" size="sm" onClick={refresh}>
            Retry
          </Button>
        </div>
      ) : null}

      {!folders.available ? (
        <div className="flex gap-3 rounded-xl border border-warning/30 bg-warning/5 p-4 text-sm">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" />
          <p>
            No sites folder mounted. Set <code>SITES_PATH</code> and mount it at{" "}
            <code>/sites</code> to add static sites. See <code>docs/static-sites.md</code> for the
            steps.
          </p>
        </div>
      ) : null}

      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <SearchInput
            value={query}
            onValueChange={setQuery}
            label="Search static sites"
            placeholder="Domain or folder"
          />
          {canWrite ? (
            <Button size="sm" onClick={() => setDialog({ open: true, site: null })}>
              <Plus /> New static site
            </Button>
          ) : (
            <p className="text-muted-foreground text-sm">
              Read-only — ask an admin to make changes.
            </p>
          )}
        </div>
        {isMobile ? (
          <RowCards loading={loading} isEmpty={visible.length === 0} empty={emptyMessage}>
            {visible.map((site) => (
              <RowCard
                key={site.id}
                title={
                  <DomainLinks domains={site.domain_names} secure={site.certificate_id != null} />
                }
                meta={
                  <EnabledToggle
                    checked={site.enabled}
                    name={site.domain_names[0]}
                    onToggle={(next) => setEnabled(site, next)}
                    disabled={!canWrite}
                  />
                }
                facts={[
                  { label: "Folder", value: <FolderCell site={site} /> },
                  { label: "Access", value: accessLabel(site) },
                  { label: "TLS", value: tls(site) },
                ]}
                actions={
                  canWrite ? (
                    <>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Edit ${site.domain_names[0]}`}
                        onClick={() => setDialog({ open: true, site })}
                      >
                        <Pencil />
                      </Button>
                      <Button
                        variant="ghost-destructive"
                        size="icon-sm"
                        aria-label={`Delete ${site.domain_names[0]}`}
                        onClick={() => setToDelete(site)}
                      >
                        <Trash2 />
                      </Button>
                    </>
                  ) : null
                }
              />
            ))}
          </RowCards>
        ) : (
          <div className="bg-card text-card-foreground rounded-xl border shadow-xs">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Domains</TableHead>
                  <TableHead>Folder</TableHead>
                  <TableHead>SSL</TableHead>
                  <TableHead>Access</TableHead>
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
                      {emptyMessage}
                    </TableCell>
                  </TableRow>
                ) : (
                  visible.map((site) => (
                    <TableRow key={site.id}>
                      <TableCell className="font-medium">
                        <DomainLinks
                          domains={site.domain_names}
                          secure={site.certificate_id != null}
                        />
                      </TableCell>
                      <TableCell>
                        <FolderCell site={site} />
                      </TableCell>
                      <TableCell>
                        {tls(site) ?? <span className="text-muted-foreground">—</span>}
                      </TableCell>
                      <TableCell>{accessLabel(site)}</TableCell>
                      <TableCell>
                        <EnabledToggle
                          checked={site.enabled}
                          name={site.domain_names[0]}
                          onToggle={(next) => setEnabled(site, next)}
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
                                aria-label={`Edit ${site.domain_names[0]}`}
                                onClick={() => setDialog({ open: true, site })}
                              >
                                <Pencil />
                              </Button>
                              <Button
                                variant="ghost-destructive"
                                size="icon-sm"
                                aria-label={`Delete ${site.domain_names[0]}`}
                                onClick={() => setToDelete(site)}
                              >
                                <Trash2 />
                              </Button>
                            </>
                          ) : null}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        )}
      </div>

      {dialog.open ? (
        <StaticSiteDialog
          key={dialog.site?.id ?? "new-site"}
          open
          onOpenChange={(open) => !open && setDialog({ open: false, site: null })}
          site={dialog.site}
          certificates={certs}
          lists={lists}
          folders={folders}
          onSaved={refresh}
        />
      ) : null}
      {toDelete ? (
        <ConfirmDeleteDialog
          open
          onOpenChange={(open) => !open && setToDelete(null)}
          title="Delete static site?"
          description={`This removes ${toDelete.domain_names.join(", ")} and regenerates the nginx config. The files in its folder are left alone.`}
          onConfirm={async () => {
            await staticSites.remove(toDelete.id);
          }}
          onDeleted={refresh}
        />
      ) : null}
    </div>
  );
}
