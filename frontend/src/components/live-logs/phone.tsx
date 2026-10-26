"use client";

import { Copy, Download, MoreHorizontal, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { toText, type LiveLine } from "@/components/live-logs/lib";
import { LineDetail } from "@/components/live-logs/log-rows";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetTitle,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

/** The page's pieces that only a phone gets: the table's controls, moved to where a thumb is. */

const PANEL = "max-h-[85vh] gap-3 rounded-t-2xl p-4";

/** A row of buttons, one pressed: a choice of a few values without opening a list. */
function Choice({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  /** value → label; "" is the "any" choice. */
  options: [string, string][];
  onChange: (value: string) => void;
}) {
  return (
    <fieldset className="flex flex-col gap-1.5">
      <legend className="mb-1.5 text-xs text-muted-foreground">{label}</legend>
      <div className="flex flex-wrap gap-1.5">
        {options.map(([v, text]) => (
          <Button
            key={v}
            type="button"
            variant="outline"
            size="sm"
            aria-pressed={value === v}
            onClick={() => onChange(v)}
            className={cn(
              "min-w-12 flex-1",
              value === v && "border-primary text-primary",
            )}
          >
            {text}
          </Button>
        ))}
      </div>
    </fieldset>
  );
}

/**
 * The status and node filters. Each is passed only where it applies: status
 * on Requests, node once a second node has reported.
 */
export function FiltersSheet({
  open,
  onOpenChange,
  status,
  statuses,
  onStatus,
  node,
  nodes,
  onNode,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  status: string;
  statuses: readonly string[] | null;
  onStatus: (status: string) => void;
  node: string;
  nodes: readonly string[] | null;
  onNode: (node: string) => void;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="bottom" showCloseButton={false} className={PANEL}>
        <SheetTitle>Filters</SheetTitle>
        {statuses ? (
          <Choice
            label="Status"
            value={status}
            options={[
              ["", "Any"],
              ...statuses.map((s): [string, string] => [s, s]),
            ]}
            onChange={onStatus}
          />
        ) : null}
        {nodes ? (
          <Choice
            label="Node"
            value={node}
            options={[
              ["", "All"],
              ...nodes.map((n): [string, string] => [n, n]),
            ]}
            onChange={onNode}
          />
        ) : null}
        <div className="mt-1 flex gap-2">
          <Button
            type="button"
            variant="outline"
            className="flex-1"
            onClick={() => {
              onStatus("");
              onNode("");
            }}
          >
            Reset
          </Button>
          <SheetClose render={<Button type="button" className="flex-1" />}>
            Done
          </SheetClose>
        </div>
      </SheetContent>
    </Sheet>
  );
}

async function copyLine(line: LiveLine) {
  try {
    await navigator.clipboard.writeText(toText([line]));
    toast.success("Line copied");
  } catch {
    toast.error("Could not copy. Select the text and copy it by hand.");
  }
}

/** Every field of the tapped line, from the bottom of the screen. */
export function LineSheet({
  line,
  hostLabel,
  onClose,
}: {
  line: LiveLine | null;
  hostLabel: string;
  onClose: () => void;
}) {
  return (
    <Sheet
      open={line !== null}
      onOpenChange={(open) => (open ? undefined : onClose())}
    >
      <SheetContent side="bottom" showCloseButton={false} className={PANEL}>
        <SheetTitle>Line details</SheetTitle>
        {line ? (
          <>
            <div className="min-h-0 overflow-auto">
              <LineDetail line={line} hostLabel={hostLabel} />
            </div>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                className="flex-1"
                onClick={() => void copyLine(line)}
              >
                <Copy /> Copy line
              </Button>
              <SheetClose
                render={
                  <Button type="button" variant="outline" className="flex-1" />
                }
              >
                Close
              </SheetClose>
            </div>
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

/** Save and Clear, which the table's footer holds on a wider screen. */
export function MoreMenu({
  onSave,
  canSave,
  onClear,
}: {
  onSave: () => void;
  canSave: boolean;
  onClear: () => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            type="button"
            variant="outline"
            size="icon-sm"
            aria-label="More actions"
          />
        }
      >
        <MoreHorizontal />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-52">
        <DropdownMenuItem onClick={onSave} disabled={!canSave}>
          <Download /> Save what&apos;s on screen
        </DropdownMenuItem>
        <DropdownMenuItem onClick={onClear}>
          <Trash2 /> Clear
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
