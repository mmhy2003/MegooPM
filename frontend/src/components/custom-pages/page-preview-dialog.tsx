"use client";

import { useEffect, useState } from "react";
import { Pencil } from "lucide-react";

import { customPages, type CustomPageSummary } from "@/lib/api";
import { describeError } from "@/components/custom-pages/lib";
import { PagePreview } from "@/components/custom-pages/page-preview";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Read-only look at one custom page.
 *
 * Open while mounted: the list renders it only when a page is selected, the
 * same way it renders the delete confirmation.
 *
 * The list carries summaries — a size, not the document — so the HTML is
 * fetched here rather than passed in.
 */
export function PagePreviewDialog({
  page,
  onOpenChange,
  onEdit,
}: {
  page: CustomPageSummary;
  onOpenChange: (open: boolean) => void;
  onEdit: () => void;
}) {
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const full = await customPages.get(page.id);
        if (active) setHtml(full.html);
      } catch (err) {
        if (active) setError(describeError(err).message);
      }
    })();
    return () => {
      active = false;
    };
  }, [page.id]);

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="flex max-w-4xl flex-col">
        <DialogHeader>
          <DialogTitle>{page.name}</DialogTitle>
          <DialogDescription>
            {page.description || "Shown exactly as a visitor would see it."}
          </DialogDescription>
        </DialogHeader>

        {error ? (
          <p role="alert" className="text-destructive text-sm">
            {error}
          </p>
        ) : html === null ? (
          <Skeleton className="h-[60vh] w-full" />
        ) : (
          <PagePreview html={html} className="h-[60vh] w-full rounded-lg border bg-white" />
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onEdit}>
            <Pencil /> Edit page
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
