"use client";

import { useMemo, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import {
  LB_METHODS,
  type UpstreamContext,
  LB_METHOD_LABELS,
  upstreams,
  type Backend,
  type BackendCreate,
  type BackendUpdate,
  type LoadBalanceMethod,
  type Upstream,
} from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/** An editable backend row. Numeric fields are held as strings while editing. */
/** Plain-language names for the nginx contexts a pool may serve. */
const CONTEXT_LABELS: Record<UpstreamContext, string> = {
  http: "HTTP only (proxy hosts)",
  stream: "Streams only (TCP/UDP)",
  both: "Both",
};

interface BackendRow {
  key: string;
  id?: number;
  host: string;
  port: string;
  weight: string;
  max_fails: string;
  fail_timeout_seconds: string;
  backup: boolean;
  down: boolean;
  enabled: boolean;
}

let rowSeq = 0;
function newRow(): BackendRow {
  rowSeq += 1;
  return {
    key: `new-${rowSeq}`,
    host: "",
    port: "80",
    weight: "1",
    max_fails: "1",
    fail_timeout_seconds: "10",
    backup: false,
    down: false,
    enabled: true,
  };
}

function rowFromBackend(b: Backend): BackendRow {
  return {
    key: `existing-${b.id}`,
    id: b.id,
    host: b.host,
    port: String(b.port),
    weight: String(b.weight ?? 1),
    max_fails: String(b.max_fails ?? 1),
    fail_timeout_seconds: String(b.fail_timeout_seconds ?? 10),
    backup: b.backup ?? false,
    down: b.down ?? false,
    enabled: b.enabled ?? true,
  };
}

function toNumber(value: string): number {
  return Number.parseInt(value, 10);
}

function rowToCreate(row: BackendRow): BackendCreate {
  return {
    host: row.host.trim(),
    port: toNumber(row.port),
    weight: toNumber(row.weight),
    max_fails: toNumber(row.max_fails),
    fail_timeout_seconds: toNumber(row.fail_timeout_seconds),
    backup: row.backup,
    down: row.down,
    enabled: row.enabled,
  };
}

/** Fields on an existing backend that differ from the edited row. */
function backendDiff(original: Backend, row: BackendRow): BackendUpdate {
  const next = rowToCreate(row);
  const changes: BackendUpdate = {};
  if (next.host !== original.host) changes.host = next.host;
  if (next.port !== original.port) changes.port = next.port;
  if (next.weight !== (original.weight ?? 1)) changes.weight = next.weight;
  if (next.max_fails !== (original.max_fails ?? 1)) changes.max_fails = next.max_fails;
  if (next.fail_timeout_seconds !== (original.fail_timeout_seconds ?? 10))
    changes.fail_timeout_seconds = next.fail_timeout_seconds;
  if (next.backup !== (original.backup ?? false)) changes.backup = next.backup;
  if (next.down !== (original.down ?? false)) changes.down = next.down;
  if (next.enabled !== (original.enabled ?? true)) changes.enabled = next.enabled;
  return changes;
}

function validateRow(row: BackendRow): string | null {
  const host = row.host.trim();
  if (!host) return "Backend host is required.";
  if (/\s/.test(host)) return "Backend host must not contain spaces.";
  const port = toNumber(row.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535)
    return `Port for ${host} must be between 1 and 65535.`;
  for (const [field, label] of [
    ["weight", "Weight"],
    ["max_fails", "Max fails"],
    ["fail_timeout_seconds", "Fail timeout"],
  ] as const) {
    const n = toNumber(row[field]);
    if (!Number.isInteger(n) || n < 0) return `${label} for ${host} must be 0 or greater.`;
  }
  return null;
}

export function UpstreamDialog({
  open,
  onOpenChange,
  upstream,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Existing pool to edit, or `null`/`undefined` to create a new one. */
  upstream?: Upstream | null;
  onSaved: () => void;
}) {
  const isEdit = Boolean(upstream);
  // Seeded from props on mount; the parent remounts this dialog (keyed) per
  // target pool, so no reset-on-open effect is needed.
  const [name, setName] = useState(upstream?.name ?? "");
  const [description, setDescription] = useState(upstream?.description ?? "");
  const [lbMethod, setLbMethod] = useState<LoadBalanceMethod>(
    upstream?.lb_method ?? "round_robin",
  );
  const [context, setContext] = useState<UpstreamContext>(upstream?.context ?? "http");
  const [enabled, setEnabled] = useState(upstream?.enabled ?? true);

  // ip_hash is an http-only directive. Offering it for a stream-capable pool
  // would only earn a 422 on save, so the list shrinks with the context.
  const methods = context === "http" ? LB_METHODS : LB_METHODS.filter((m) => m !== "ip_hash");

  function changeContext(next: UpstreamContext) {
    setContext(next);
    // Drop a now-illegal selection rather than letting the user submit it.
    if (next !== "http" && lbMethod === "ip_hash") setLbMethod("round_robin");
  }
  const [rows, setRows] = useState<BackendRow[]>(() =>
    upstream?.backends?.length ? upstream.backends.map(rowFromBackend) : [newRow()],
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const originalBackends = useMemo(() => {
    const map = new Map<number, Backend>();
    for (const b of upstream?.backends ?? []) map.set(b.id, b);
    return map;
  }, [upstream]);

  function updateRow(key: string, patch: Partial<BackendRow>) {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }

  function removeRow(key: string) {
    setRows((prev) => prev.filter((r) => r.key !== key));
  }

  async function handleSubmit() {
    setError(null);
    if (!name.trim()) {
      setError("Pool name is required.");
      return;
    }
    if (rows.length === 0) {
      setError("Add at least one backend server.");
      return;
    }
    for (const row of rows) {
      const rowError = validateRow(row);
      if (rowError) {
        setError(rowError);
        return;
      }
    }

    setSaving(true);
    try {
      if (!isEdit || !upstream) {
        await upstreams.create({
          name: name.trim(),
          description: description.trim(),
          lb_method: lbMethod,
          context,
          enabled,
          backends: rows.map(rowToCreate),
        });
      } else {
        await upstreams.update(upstream.id, {
          name: name.trim(),
          description: description.trim(),
          lb_method: lbMethod,
          context,
          enabled,
        });
        // Reconcile backends: delete removed, patch changed, add new.
        const keptIds = new Set(rows.filter((r) => r.id).map((r) => r.id as number));
        for (const id of originalBackends.keys()) {
          if (!keptIds.has(id)) await upstreams.removeBackend(upstream.id, id);
        }
        for (const row of rows) {
          if (row.id) {
            const original = originalBackends.get(row.id);
            const changes = original ? backendDiff(original, row) : {};
            if (Object.keys(changes).length > 0) {
              await upstreams.updateBackend(upstream.id, row.id, changes);
            }
          } else {
            await upstreams.addBackend(upstream.id, rowToCreate(row));
          }
        }
      }
      toast.success(isEdit ? "Upstream pool updated" : "Upstream pool created");
      onOpenChange(false);
      onSaved();
    } catch (err) {
      const described = describeError(err);
      setError(described.message);
      toast.error(described.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit upstream pool" : "New upstream pool"}</DialogTitle>
          <DialogDescription>
            A pool is a load-balanced set of backend servers a proxy host forwards to.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="pool-name">Name</Label>
            <Input
              id="pool-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="app-servers"
              disabled={saving}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="pool-lb">Load-balancing method</Label>
            <Select
              value={lbMethod}
              onValueChange={(value) => setLbMethod(value as LoadBalanceMethod)}
              items={LB_METHOD_LABELS}
            >
              <SelectTrigger id="pool-lb" disabled={saving}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {methods.map((method) => (
                  <SelectItem key={method} value={method}>
                    {LB_METHOD_LABELS[method]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="pool-context">Context</Label>
            <Select
              value={context}
              onValueChange={(value) => changeContext(value as UpstreamContext)}
              items={CONTEXT_LABELS}
            >
              <SelectTrigger id="pool-context" disabled={saving}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(["http", "stream", "both"] as const).map((c) => (
                  <SelectItem key={c} value={c}>
                    {CONTEXT_LABELS[c]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Where this pool may be attached. Streams cannot use IP hash.
            </p>
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="pool-desc">Description</Label>
            <Input
              id="pool-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional"
              disabled={saving}
            />
          </div>
          <label className="flex items-center gap-2 sm:col-span-2">
            <Switch checked={enabled} onCheckedChange={setEnabled} disabled={saving} />
            <span className="text-sm font-medium">Enabled</span>
          </label>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium">Backends</h3>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setRows((prev) => [...prev, newRow()])}
              disabled={saving}
            >
              <Plus /> Add backend
            </Button>
          </div>

          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="min-w-40">Host</TableHead>
                  <TableHead className="w-24">Port</TableHead>
                  <TableHead className="w-20">Weight</TableHead>
                  <TableHead className="w-24">Max fails</TableHead>
                  <TableHead className="w-28">Fail timeout</TableHead>
                  <TableHead className="w-16 text-center">Down</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={7} className="text-center text-muted-foreground">
                      No backends yet — add one to route traffic.
                    </TableCell>
                  </TableRow>
                ) : (
                  rows.map((row) => (
                    <TableRow key={row.key}>
                      <TableCell>
                        <Input
                          aria-label="Backend host"
                          value={row.host}
                          onChange={(e) => updateRow(row.key, { host: e.target.value })}
                          placeholder="10.0.0.1"
                          disabled={saving}
                        />
                      </TableCell>
                      <TableCell>
                        <Input
                          aria-label="Backend port"
                          type="number"
                          min={1}
                          max={65535}
                          value={row.port}
                          onChange={(e) => updateRow(row.key, { port: e.target.value })}
                          disabled={saving}
                        />
                      </TableCell>
                      <TableCell>
                        <Input
                          aria-label="Weight"
                          type="number"
                          min={0}
                          value={row.weight}
                          onChange={(e) => updateRow(row.key, { weight: e.target.value })}
                          disabled={saving}
                        />
                      </TableCell>
                      <TableCell>
                        <Input
                          aria-label="Max fails"
                          type="number"
                          min={0}
                          value={row.max_fails}
                          onChange={(e) => updateRow(row.key, { max_fails: e.target.value })}
                          disabled={saving}
                        />
                      </TableCell>
                      <TableCell>
                        <Input
                          aria-label="Fail timeout (seconds)"
                          type="number"
                          min={0}
                          value={row.fail_timeout_seconds}
                          onChange={(e) =>
                            updateRow(row.key, { fail_timeout_seconds: e.target.value })
                          }
                          disabled={saving}
                        />
                      </TableCell>
                      <TableCell className="text-center">
                        <Switch
                          aria-label="Administratively down"
                          checked={row.down}
                          onCheckedChange={(v) => updateRow(row.key, { down: v })}
                          disabled={saving}
                        />
                      </TableCell>
                      <TableCell>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          aria-label="Remove backend"
                          onClick={() => removeRow(row.key)}
                          disabled={saving}
                        >
                          <Trash2 />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
          <p className="text-xs text-muted-foreground">
            Health checks are passive: a backend is taken out of rotation after{" "}
            <span className="font-medium">Max fails</span> failed attempts within the{" "}
            <span className="font-medium">Fail timeout</span> window.
          </p>
        </div>

        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={saving}>
            {saving ? "Saving…" : isEdit ? "Save changes" : "Create pool"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
