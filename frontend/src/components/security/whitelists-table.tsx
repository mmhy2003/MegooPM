"use client";

import { Pencil, Trash2 } from "lucide-react";

import { EnabledToggle } from "@/components/hosts/enabled-toggle";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { WHITELIST_KIND_LABELS, type Whitelist } from "@/lib/api";

/** "1 IP, 2 CIDRs" — a one-entry whitelist is the common case, so plurals matter. */
function coverage(row: Whitelist): string {
  if (row.kind === "expression") {
    const n = row.expressions.length;
    return `${n} expression${n === 1 ? "" : "s"}`;
  }
  const parts: string[] = [];
  if (row.ips.length) parts.push(`${row.ips.length} IP${row.ips.length === 1 ? "" : "s"}`);
  if (row.cidrs.length) parts.push(`${row.cidrs.length} CIDR${row.cidrs.length === 1 ? "" : "s"}`);
  return parts.join(", ");
}

export function WhitelistsTable({
  rows,
  query = "",
  onClearSearch,
  onToggle,
  onEdit,
  onDelete,
}: {
  rows: Whitelist[];
  /** The active search, so the empty state can say which kind of empty it is. */
  query?: string;
  onClearSearch?: () => void;
  onToggle: (row: Whitelist, next: boolean) => Promise<void>;
  onEdit: (row: Whitelist) => void;
  onDelete: (row: Whitelist) => void;
}) {
  if (rows.length === 0) {
    const searching = query.trim();
    return (
      <p className="text-muted-foreground rounded-lg border border-dashed p-8 text-center text-sm">
        {searching ? (
          <>
            No whitelists match “{searching}”.{" "}
            <Button
              variant="link"
              size="sm"
              className="h-auto p-0 align-baseline"
              onClick={onClearSearch}
            >
              Clear search
            </Button>
          </>
        ) : (
          "No whitelists yet. Add one to stop CrowdSec acting on traffic from an address you trust."
        )}
      </p>
    );
  }

  return (
    <div className="bg-card text-card-foreground rounded-xl border shadow-xs">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Kind</TableHead>
            <TableHead>Reason</TableHead>
            <TableHead>Covers</TableHead>
            <TableHead>Enabled</TableHead>
            <TableHead className="w-24" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.id}>
              <TableCell className="font-medium">{row.name}</TableCell>
              <TableCell>
                <Badge variant={row.kind === "expression" ? "outline" : "secondary"}>
                  {WHITELIST_KIND_LABELS[row.kind]}
                </Badge>
              </TableCell>
              <TableCell className="text-muted-foreground">{row.reason}</TableCell>
              <TableCell>{coverage(row)}</TableCell>
              <TableCell>
                <EnabledToggle
                  checked={row.enabled}
                  name={row.name}
                  onToggle={(next) => onToggle(row, next)}
                />
              </TableCell>
              <TableCell className="text-right">
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label={`Edit ${row.name}`}
                  onClick={() => onEdit(row)}
                >
                  <Pencil className="size-4" />
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label={`Delete ${row.name}`}
                  onClick={() => onDelete(row)}
                >
                  <Trash2 className="size-4" />
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
