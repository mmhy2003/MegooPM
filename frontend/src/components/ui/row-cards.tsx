"use client";

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * The phone-width shape of a list page.
 *
 * Every list page is the same six-column table: an identity, a few
 * attributes, a status, and actions. Six columns do not fit in 360px — the
 * identity breaks one character per line and the actions fall off the edge —
 * so below the breakpoint each row becomes a card with the same four parts,
 * laid out top to bottom. Pages pick the layout with `useIsMobile()` and map
 * their columns onto {@link RowCard}'s slots; this component owns the
 * scaffolding they would otherwise each repeat: the bordered container, the
 * loading skeleton, the empty state, and the divided list.
 */
export function RowCards({
  loading,
  isEmpty,
  empty,
  children,
  className,
}: {
  loading: boolean;
  isEmpty: boolean;
  /** Shown instead of the list when there is nothing to list. */
  empty: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("bg-card text-card-foreground rounded-xl border shadow-xs", className)}>
      {loading ? (
        <div className="space-y-3 p-4">
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-4 w-3/5" />
        </div>
      ) : isEmpty ? (
        <div className="text-muted-foreground px-4 py-10 text-center text-sm">{empty}</div>
      ) : (
        <ul className="divide-y">{children}</ul>
      )}
    </div>
  );
}

export interface RowFact {
  label: string;
  /** `null`/`undefined` render as an em dash: a blank beside a label reads as a bug. */
  value: ReactNode;
}

/**
 * One row of a list page, as a card.
 *
 * `title` is the identity — a name, a domain, a port. `meta` sits on the same
 * line, right-aligned: the status toggle or badge, or a time. `facts` are the
 * remaining columns as label/value pairs. `actions` are the row's buttons.
 *
 * `onPress` makes the title a button rather than the whole card clickable, so
 * the action buttons inside are never nested in another control — nested
 * interactive elements are invalid HTML and confuse screen readers.
 */
export function RowCard({
  title,
  meta,
  facts,
  actions,
  onPress,
  className,
}: {
  title: ReactNode;
  meta?: ReactNode;
  facts?: RowFact[];
  actions?: ReactNode;
  onPress?: () => void;
  className?: string;
}) {
  return (
    <li className={cn("space-y-2 p-4 text-sm", className)}>
      <div className="flex items-start justify-between gap-3">
        {/* break-words, not break-all: a domain wraps where it must, never one
            character per line. */}
        {onPress ? (
          <button
            type="button"
            onClick={onPress}
            className="min-w-0 text-left font-medium break-words hover:underline"
          >
            {title}
          </button>
        ) : (
          <div className="min-w-0 font-medium break-words">{title}</div>
        )}
        {meta != null ? <div className="flex shrink-0 items-center gap-2">{meta}</div> : null}
      </div>

      {facts && facts.length > 0 ? (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
          {facts.map((fact) => (
            <div key={fact.label} className="contents">
              <dt className="text-muted-foreground whitespace-nowrap">{fact.label}</dt>
              <dd className="min-w-0 break-words">
                {fact.value == null || fact.value === "" ? (
                  <span className="text-muted-foreground">—</span>
                ) : (
                  fact.value
                )}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}

      {actions != null ? <div className="flex justify-end gap-1">{actions}</div> : null}
    </li>
  );
}
