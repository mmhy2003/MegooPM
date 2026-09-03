"use client";

import { Search, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/**
 * The search box shared by every list page.
 *
 * `type="search"` for the `searchbox` role, with WebKit's native cancel button
 * hidden — two clear buttons on one input is worse than none.
 */
export function SearchInput({
  value,
  onValueChange,
  label,
  placeholder = "Search",
  className,
}: {
  value: string;
  onValueChange: (next: string) => void;
  /** Accessible name. One box per page, so name it after the page. */
  label: string;
  placeholder?: string;
  className?: string;
}) {
  return (
    <div className={cn("relative w-full sm:max-w-xs", className)}>
      <Search
        aria-hidden="true"
        className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
      />
      <Input
        type="search"
        aria-label={label}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onValueChange(e.target.value)}
        className="pr-8 pl-8 [&::-webkit-search-cancel-button]:hidden"
      />
      {value ? (
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={`Clear ${label.toLowerCase()}`}
          className="absolute top-1/2 right-0.5 -translate-y-1/2"
          onClick={() => onValueChange("")}
        >
          <X />
        </Button>
      ) : null}
    </div>
  );
}
