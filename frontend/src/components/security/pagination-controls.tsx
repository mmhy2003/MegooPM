"use client";

import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from "lucide-react";

import { PAGE_SIZE_OPTIONS } from "@/lib/api";
import { pageCount, rangeLabel } from "@/components/security/lib";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/**
 * Server-driven pagination footer for the Alerts/Decisions tables (MEG-44).
 *
 * The parent owns the fetch; this only reflects `page`/`pageSize`/`total` and
 * reports intent via `onPageChange`/`onPageSizeChange`. It never fetches or
 * clamps — the parent re-fetches and the response's `total` keeps the range
 * honest. Prev/Next disable at the edges; controls disable while `loading`.
 */
export function PaginationControls({
  page,
  pageSize,
  total,
  loading = false,
  idPrefix,
  onPageChange,
  onPageSizeChange,
}: {
  page: number;
  pageSize: number;
  total: number;
  loading?: boolean;
  /** Namespaces the page-size <label>/<select> so both tabs stay accessible. */
  idPrefix: string;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
}) {
  const last = pageCount(total, pageSize);
  const atFirst = page <= 1;
  const atLast = page >= last;

  return (
    <div className="flex flex-col items-center justify-between gap-3 px-1 sm:flex-row">
      <div className="flex items-center gap-2">
        <Label htmlFor={`${idPrefix}-page-size`} className="text-xs text-muted-foreground">
          Rows per page
        </Label>
        <Select
          value={String(pageSize)}
          onValueChange={(v) => v && onPageSizeChange(Number(v))}
        >
          <SelectTrigger
            id={`${idPrefix}-page-size`}
            size="sm"
            className="w-[4.5rem]"
            disabled={loading}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PAGE_SIZE_OPTIONS.map((n) => (
              <SelectItem key={n} value={String(n)}>
                {n}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex items-center gap-3">
        <span
          className="text-xs tabular-nums text-muted-foreground"
          aria-live="polite"
        >
          {rangeLabel(page, pageSize, total)}
        </span>
        <div className="flex items-center gap-1">
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="First page"
            disabled={loading || atFirst}
            onClick={() => onPageChange(1)}
          >
            <ChevronsLeft />
          </Button>
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="Previous page"
            disabled={loading || atFirst}
            onClick={() => onPageChange(page - 1)}
          >
            <ChevronLeft />
          </Button>
          <span className="px-1 text-xs tabular-nums text-muted-foreground">
            Page {Math.min(page, last)} of {last}
          </span>
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="Next page"
            disabled={loading || atLast}
            onClick={() => onPageChange(page + 1)}
          >
            <ChevronRight />
          </Button>
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="Last page"
            disabled={loading || atLast}
            onClick={() => onPageChange(last)}
          >
            <ChevronsRight />
          </Button>
        </div>
      </div>
    </div>
  );
}
