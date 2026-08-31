"use client";

import { Plus, Trash2 } from "lucide-react";

import { HTTP_SCHEMES, type HttpScheme, type Upstream } from "@/lib/api";
import {
  newLocationRow,
  type LocationRow,
  type TargetMode,
} from "@/components/proxy-hosts/lib";
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

/** One label for a pool, used by both the option and the trigger. */
function poolLabel(pool: Upstream): string {
  return `${pool.name} (${pool.backends?.length ?? 0} backends)`;
}

const KIND_LABELS: Record<TargetMode, string> = {
  pool: "Pool",
  host: "Single host",
};

/** Pool or single backend, as a compact select rather than radios.
 *
 * These are table rows, and a radio group per row would wreck a dense table.
 * A select is one control in one cell and reads the same on every row.
 */
function KindSelect({
  value,
  onChange,
  label,
  disabled,
}: {
  value: TargetMode;
  onChange: (v: TargetMode) => void;
  label: string;
  disabled: boolean;
}) {
  return (
    <Select value={value} onValueChange={(v) => onChange(v as TargetMode)} items={KIND_LABELS}>
      <SelectTrigger aria-label={label} disabled={disabled}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {(["pool", "host"] as const).map((k) => (
          <SelectItem key={k} value={k}>
            {KIND_LABELS[k]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** The target cell: a pool picker, or a host and port pair. */
function TargetCell({
  mode,
  upstreamId,
  forwardHost,
  forwardPort,
  onChange,
  pools,
  disabled,
  labelPrefix,
}: {
  mode: TargetMode;
  upstreamId: string;
  forwardHost: string;
  forwardPort: string;
  onChange: (patch: {
    upstreamId?: string;
    forwardHost?: string;
    forwardPort?: string;
  }) => void;
  pools: Upstream[];
  disabled: boolean;
  labelPrefix: string;
}) {
  if (mode === "pool") {
    return (
      <PoolSelect
        value={upstreamId}
        onChange={(v) => onChange({ upstreamId: v })}
        pools={pools}
        disabled={disabled}
      />
    );
  }
  return (
    <div className="flex gap-2">
      <Input
        aria-label={`${labelPrefix} forward host`}
        value={forwardHost}
        onChange={(e) => onChange({ forwardHost: e.target.value })}
        placeholder="10.0.0.1"
        disabled={disabled}
      />
      <Input
        aria-label={`${labelPrefix} forward port`}
        type="number"
        inputMode="numeric"
        min={1}
        max={65535}
        value={forwardPort}
        onChange={(e) => onChange({ forwardPort: e.target.value })}
        placeholder="8080"
        className="w-28"
        disabled={disabled}
      />
    </div>
  );
}

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
  // Without `items` the trigger renders the raw value — the pool's id.
  const items = Object.fromEntries(pools.map((p) => [String(p.id), poolLabel(p)]));
  return (
    <Select value={value} onValueChange={(v) => onChange(v as string)} items={items}>
      <SelectTrigger aria-label="Upstream pool" disabled={disabled || noPools}>
        <SelectValue placeholder={noPools ? "No pools — create one first" : "Select a pool"} />
      </SelectTrigger>
      <SelectContent>
        {pools.map((pool) => (
          <SelectItem key={pool.id} value={String(pool.id)}>
            {poolLabel(pool)}
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
 * forwarding to its own pool or single backend, with its own scheme.
 */
export function LocationsEditor({
  rootTargetMode,
  rootUpstreamId,
  rootForwardHost,
  rootForwardPort,
  rootScheme,
  onRootChange,
  rows,
  onRowsChange,
  pools,
  disabled,
}: {
  rootTargetMode: TargetMode;
  rootUpstreamId: string;
  rootForwardHost: string;
  rootForwardPort: string;
  rootScheme: HttpScheme;
  onRootChange: (patch: {
    rootTargetMode?: TargetMode;
    rootUpstreamId?: string;
    rootForwardHost?: string;
    rootForwardPort?: string;
    rootScheme?: HttpScheme;
  }) => void;
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
              <TableHead className="w-32">Kind</TableHead>
              <TableHead>Target</TableHead>
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
                <KindSelect
                  value={rootTargetMode}
                  onChange={(v) => onRootChange({ rootTargetMode: v })}
                  label="Root target kind"
                  disabled={disabled}
                />
              </TableCell>
              <TableCell>
                <TargetCell
                  mode={rootTargetMode}
                  upstreamId={rootUpstreamId}
                  forwardHost={rootForwardHost}
                  forwardPort={rootForwardPort}
                  onChange={({ upstreamId, forwardHost, forwardPort }) =>
                    onRootChange({
                      ...(upstreamId !== undefined && { rootUpstreamId: upstreamId }),
                      ...(forwardHost !== undefined && { rootForwardHost: forwardHost }),
                      ...(forwardPort !== undefined && { rootForwardPort: forwardPort }),
                    })
                  }
                  pools={pools}
                  disabled={disabled}
                  labelPrefix="Root"
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
                  <KindSelect
                    value={row.targetMode}
                    onChange={(v) => updateRow(row.key, { targetMode: v })}
                    label="Location target kind"
                    disabled={disabled}
                  />
                </TableCell>
                <TableCell>
                  <TargetCell
                    mode={row.targetMode}
                    upstreamId={row.upstreamId}
                    forwardHost={row.forwardHost}
                    forwardPort={row.forwardPort}
                    onChange={(patch) => updateRow(row.key, patch)}
                    pools={pools}
                    disabled={disabled}
                    labelPrefix="Location"
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
