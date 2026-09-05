"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { FileCode2, Pencil, Plus, Trash2 } from "lucide-react";

import { customPages, type CustomPageSummary } from "@/lib/api";
import { describeError, formatBytes } from "@/components/custom-pages/lib";
import { ConfirmDeleteDialog } from "@/components/proxy-hosts/confirm-delete-dialog";
import { PagePreviewDialog } from "@/components/custom-pages/page-preview-dialog";
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

/** Short absolute date; the exact time is rarely what you want in a table. */
function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/**
 * Index of the custom pages. The editor lives on its own route rather than in a
 * dialog — a code editor plus a live preview needs the width.
 */
export function CustomPagesView() {
  const router = useRouter();
  const [pages, setPages] = useState<CustomPageSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [deletePage, setDeletePage] = useState<CustomPageSummary | null>(null);
  const [previewPage, setPreviewPage] = useState<CustomPageSummary | null>(null);
  const [query, setQuery] = useState("");

  const visible = useMemo(
    () => filterBySearch(pages, query, (p) => [p.name, p.description]),
    [pages, query],
  );

  const load = useCallback(async () => {
    try {
      setPages(await customPages.list());
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

  // The IIFE keeps the effect callback itself synchronous; `load` awaits before
  // any setState, so nothing updates state synchronously in the effect body.
  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <FileCode2 className="size-5" />
        </div>
        <div className="flex-1">
          <h2 className="text-xl font-semibold tracking-tight">Custom Pages</h2>
          <p className="text-sm text-muted-foreground">
            HTML pages you author here and reference elsewhere. Images are
            embedded in the document, so a page is a single self-contained file.
          </p>
        </div>
        <Button size="sm" onClick={() => router.push("/custom-pages/new")}>
          <Plus /> New page
        </Button>
      </div>

      {loadError ? (
        <div className="flex flex-col items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
          <p className="text-sm text-destructive" role="alert">
            Couldn&apos;t load custom pages: {loadError}
          </p>
          <Button variant="outline" size="sm" onClick={refresh}>
            Retry
          </Button>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <SearchInput
          value={query}
          onValueChange={setQuery}
          label="Search custom pages"
          placeholder="Page name or description"
        />
      </div>

      <div className="bg-card text-card-foreground rounded-xl border shadow-xs">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Description</TableHead>
              <TableHead className="w-24">Size</TableHead>
              <TableHead className="w-32">Updated</TableHead>
              <TableHead className="w-24 text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <LoadingRows cols={5} />
            ) : visible.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="py-10 text-center text-muted-foreground">
                  {query.trim() ? (
                    <>
                      No custom pages match “{query.trim()}”.{" "}
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
                    "No custom pages yet. Create one to design a page you can point a host at."
                  )}
                </TableCell>
              </TableRow>
            ) : (
              visible.map((page) => (
                <TableRow
                  key={page.id}
                  className="cursor-pointer"
                  onClick={() => setPreviewPage(page)}
                >
                  <TableCell className="font-medium">
                    {/* A button, not just a clickable row: the row's onClick is
                        invisible to the keyboard and to screen readers. */}
                    <button
                      type="button"
                      aria-label={`Preview ${page.name}`}
                      className="rounded-sm text-left hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                      onClick={(event) => {
                        event.stopPropagation();
                        setPreviewPage(page);
                      }}
                    >
                      {page.name}
                    </button>
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {page.description || "—"}
                  </TableCell>
                  <TableCell className="tabular-nums">{formatBytes(page.size_bytes)}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {formatDate(page.updated_at)}
                  </TableCell>
                  <TableCell onClick={(event) => event.stopPropagation()}>
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Edit ${page.name}`}
                        onClick={() => router.push(`/custom-pages/${page.id}`)}
                      >
                        <Pencil />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Delete ${page.name}`}
                        onClick={() => setDeletePage(page)}
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

      {previewPage ? (
        <PagePreviewDialog
          page={previewPage}
          onOpenChange={(open) => !open && setPreviewPage(null)}
          onEdit={() => router.push(`/custom-pages/${previewPage.id}`)}
        />
      ) : null}

      {deletePage ? (
        <ConfirmDeleteDialog
          open
          onOpenChange={(open) => !open && setDeletePage(null)}
          title="Delete custom page?"
          description={`This permanently removes “${deletePage.name}”.`}
          onConfirm={async () => {
            await customPages.remove(deletePage.id);
          }}
          onDeleted={refresh}
        />
      ) : null}
    </div>
  );
}
