"use client";

import { useMemo } from "react";
import { Combobox } from "@base-ui/react/combobox";
import { CheckIcon, ChevronsUpDownIcon, SearchIcon } from "lucide-react";

import { cn } from "@/lib/utils";

interface Option {
  value: string;
  label: string;
}

/**
 * A select whose list can be typed into.
 *
 * For the pickers whose options come from data and so grow without bound —
 * upstream pools, certificates, access lists, custom pages, DNS credentials.
 * The fixed-enum pickers (scheme, role, LB method) keep the plain `Select`: a
 * search box on a two-item list is friction, not help.
 *
 * Same contract as `Select` — `value`, `items` (value → label), `onValueChange`
 * — so swapping one for the other is a component-name change. Closed, it looks
 * like a select trigger; open, a filter input sits above the list. The filter
 * is base-ui's collator match: case-insensitive, anywhere in the label, so
 * "relay" finds "mail-relay" and "API" finds "api-pool".
 *
 * The trigger carries `role="combobox"` explicitly. base-ui renders it as a
 * plain button, but every dialog test — and every screen reader user — already
 * finds these pickers by that role, and the swap must not move them.
 */
export function SearchableSelect({
  id,
  value,
  items,
  onValueChange,
  placeholder = "Choose…",
  searchLabel = "Search options",
  disabled,
  className,
}: {
  id?: string;
  value: string;
  /** value → label, exactly as `Select` takes it. */
  items: Record<string, string>;
  onValueChange: (value: string) => void;
  placeholder?: string;
  /** The filter input's accessible name; one per page is enough. */
  searchLabel?: string;
  disabled?: boolean;
  className?: string;
}) {
  const options = useMemo<Option[]>(
    () => Object.entries(items).map(([itemValue, label]) => ({ value: itemValue, label })),
    [items],
  );
  const selected = options.find((option) => option.value === value) ?? null;

  return (
    <Combobox.Root<Option>
      items={options}
      value={selected}
      onValueChange={(next) => onValueChange(next ? next.value : "")}
      itemToStringLabel={(option) => option.label}
      itemToStringValue={(option) => option.value}
      isItemEqualToValue={(a, b) => a.value === b.value}
      disabled={disabled}
    >
      <Combobox.Trigger
        id={id}
        role="combobox"
        disabled={disabled}
        className={cn(
          "flex h-8 w-full items-center justify-between gap-2 rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm whitespace-nowrap transition-colors outline-none select-none hover:bg-muted/40 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30 dark:hover:bg-input/50",
          className,
        )}
      >
        {/* Value takes no className; the span carries the truncation. */}
        <span className="min-w-0 truncate">
          <Combobox.Value
            placeholder={<span className="text-muted-foreground">{placeholder}</span>}
          />
        </span>
        <ChevronsUpDownIcon className="size-4 shrink-0 opacity-60" />
      </Combobox.Trigger>

      <Combobox.Portal>
        <Combobox.Positioner sideOffset={4} className="isolate z-50 outline-none">
          <Combobox.Popup className="z-50 max-h-(--available-height) min-w-(--anchor-width) origin-(--transform-origin) overflow-hidden rounded-lg bg-popover text-popover-foreground shadow-md ring-1 ring-foreground/10 outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
            <div className="flex items-center gap-2 border-b px-2.5">
              <SearchIcon className="size-4 shrink-0 text-muted-foreground" />
              <Combobox.Input
                aria-label={searchLabel}
                placeholder="Type to filter"
                className="h-8 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              />
            </div>
            <Combobox.Empty className="text-muted-foreground px-2.5 py-3 text-sm empty:hidden">
              No matches.
            </Combobox.Empty>
            <Combobox.List className="max-h-64 overflow-y-auto p-1">
              {(option: Option) => (
                <Combobox.Item
                  key={option.value}
                  value={option}
                  className="relative flex w-full cursor-default items-center gap-2 rounded-md py-1 pr-8 pl-2 text-sm outline-hidden select-none data-highlighted:bg-accent data-highlighted:text-accent-foreground"
                >
                  <span className="truncate">{option.label}</span>
                  <Combobox.ItemIndicator className="absolute right-2 flex size-4 items-center justify-center">
                    <CheckIcon className="size-4" />
                  </Combobox.ItemIndicator>
                </Combobox.Item>
              )}
            </Combobox.List>
          </Combobox.Popup>
        </Combobox.Positioner>
      </Combobox.Portal>
    </Combobox.Root>
  );
}
