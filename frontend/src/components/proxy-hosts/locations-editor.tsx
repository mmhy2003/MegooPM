"use client";

import { Plus, Trash2 } from "lucide-react";

import { HTTP_SCHEMES, type HttpScheme, type Upstream } from "@/lib/api";
import { newLocationRow, type LocationRow } from "@/components/proxy-hosts/lib";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const SCHEME_LABELS: Record<HttpScheme, string> = { http: "http", https: "https" };

function PoolSelect({
  value,
  onChange,
  pools,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  pools: Upstream[];
  disabled: boolean;
}) {
  const noPools = pools.length === 0;
  return (
    <Select value={value} onValueChange={(v) => onChange(v as string)}>
      <SelectTrigger aria-label="Upstream pool" disabled={disabled || noPools}>
        <SelectValue placeholder={noPools ? "No pools — create one first" : "Select a pool"} />
      </SelectTrigger>
      <SelectContent>
        {pools.map((pool) => (
          <SelectItem key={pool.id} value={String(pool.id)}>
            {pool.name} ({pool.backends?.length ?? 0} backends)
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function SchemeSelect({
  value,
  onChange,
  disabled,
}: {
  value: HttpScheme;
  onChange: (value: HttpScheme) => void;
  disabled: boolean;
}) {
  return (
    <Select value={value} onValueChange={(v) => onChange(v as HttpScheme)} items={SCHEME_LABELS}>
      <SelectTrigger aria-label="Forward scheme" disabled={disabled}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {HTTP_SCHEMES.map((scheme) => (
          <SelectItem key={scheme} value={scheme}>
            {SCHEME_LABELS[scheme]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/**
 * Root route (pinned to `/`) plus extra `location ^~ <path>` rows, each
 * forwarding to its own upstream pool with its own scheme.
 */
export function LocationsEditor({
  rootUpstreamId,
  rootScheme,
  onRootChange,
  rows,
  onRowsChange,
  pools,
  disabled,
}: {
  rootUpstreamId: string;
  rootScheme: HttpScheme;
  onRootChange: (patch: { rootUpstreamId?: string; rootScheme?: HttpScheme }) => void;
  rows: LocationRow[];
  onRowsChange: (rows: LocationRow[]) => void;
  pools: Upstream[];
  disabled: boolean;
}) {
  function updateRow(key: string, patch: Partial<LocationRow>) {
    onRowsChange(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">Locations</h3>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onRowsChange([...rows, newLocationRow()])}
          disabled={disabled}
        >
          <Plus /> Add location
        </Button>
      </div>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-40">Path</TableHead>
              <TableHead>Upstream pool</TableHead>
              <TableHead className="w-28">Scheme</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow>
              <TableCell>
                <Input aria-label="Root path" value="/" readOnly disabled className="font-mono" />
              </TableCell>
              <TableCell>
                <PoolSelect
                  value={rootUpstreamId}
                  onChange={(v) => onRootChange({ rootUpstreamId: v })}
                  pools={pools}
                  disabled={disabled}
                />
              </TableCell>
              <TableCell>
                <SchemeSelect
                  value={rootScheme}
                  onChange={(v) => onRootChange({ rootScheme: v })}
                  disabled={disabled}
                />
              </TableCell>
              <TableCell />
            </TableRow>
            {rows.map((row) => (
              <TableRow key={row.key}>
                <TableCell>
                  <Input
                    aria-label="Location path"
                    value={row.path}
                    onChange={(e) => updateRow(row.key, { path: e.target.value })}
                    placeholder="/api/"
                    className="font-mono"
                    disabled={disabled}
                  />
                </TableCell>
                <TableCell>
                  <PoolSelect
                    value={row.upstreamId}
                    onChange={(v) => updateRow(row.key, { upstreamId: v })}
                    pools={pools}
                    disabled={disabled}
                  />
                </TableCell>
                <TableCell>
                  <SchemeSelect
                    value={row.scheme}
                    onChange={(v) => updateRow(row.key, { scheme: v })}
                    disabled={disabled}
                  />
                </TableCell>
                <TableCell>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    aria-label="Remove location"
                    onClick={() => onRowsChange(rows.filter((r) => r.key !== row.key))}
                    disabled={disabled}
                  >
                    <Trash2 />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <p className="text-xs text-muted-foreground">
        <code>/</code> is the host&apos;s root route. Extra rows are prefix matches (
        <code>location ^~</code>) — the longest matching path wins.
      </p>
    </div>
  );
}
