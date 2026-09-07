"use client";

import { useCallback, useEffect, useState } from "react";
import { ScrollText } from "lucide-react";

import { auditLog, type AuditAction, type AuditLogEntry } from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { formatRelativeTime } from "@/components/security/lib";
import { actionTone, describeObject, summarise } from "@/components/audit-log/lib";
import { AuditDetailsDialog } from "@/components/audit-log/details-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { SearchInput } from "@/components/ui/search-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { useIsMobile } from "@/hooks/use-mobile";

const PAGE_SIZE = 50;
const ANY = "any";

const ACTIONS: { value: AuditAction; label: string }[] = [
  { value: "create", label: "Create" },
  { value: "update", label: "Update" },
  { value: "delete", label: "Delete" },
  { value: "enable", label: "Enable" },
  { value: "disable", label: "Disable" },
];

/** Every object_type the backend writes, as words.
 *
 * A fixed list rather than the distinct values in the table: a type nobody has
 * touched yet would otherwise be unfilterable, and the filter's options would
 * change as the log grows. */
const OBJECT_TYPES = [
  "access_list",
  "api_key",
  "certificate",
  "crowdsec_capi",
  "crowdsec_decision",
  "crowdsec_hub",
  "crowdsec_whitelist",
  "custom_page",
  "dead_host",
  "dns_credential",
  "error_page",
  "instance_settings",
  "proxy_host",
  "redirection_host",
  "stream",
  "upstream",
  "user",
] as const;

function label(objectType: string): string {
  return describeObject(objectType, null);
}

const TONE_CLASS: Record<ReturnType<typeof actionTone>, string> = {
  destructive: "border-destructive/40 text-destructive",
  positive: "border-emerald-500/40 text-emerald-600 dark:text-emerald-400",
  neutral: "",
};

/**
 * Every recorded mutation, newest first.
 *
 * Read-only by nature: rows are appended by the handlers that change things and
 * are never edited or deleted, so this page has no actions — only ways to find
 * a row and to see all of it.
 */
export function AuditLogView() {
  const [entries, setEntries] = useState<AuditLogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [action, setAction] = useState<string>(ANY);
  const [objectType, setObjectType] = useState<string>(ANY);
  const [actorQuery, setActorQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<AuditLogEntry | null>(null);
  // Six columns do not fit in 360px: the actor breaks one character per line
  // and Details is pushed off the edge. Below the breakpoint each entry is a
  // card instead — same data, same dialog, a shape that fits the width.
  const isMobile = useIsMobile();
  // Read once per load, not per render: "3h ago" must not shift under a
  // re-render, and calling Date.now() while rendering is impure.
  const [nowMs, setNowMs] = useState<number>(() => Date.now());

  // One request per pause, not one per keystroke.
  const actor = useDebouncedValue(actorQuery, 300);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const page = await auditLog.list({
        actor: actor || undefined,
        action: action === ANY ? undefined : (action as AuditAction),
        objectType: objectType === ANY ? undefined : objectType,
        limit: PAGE_SIZE,
        offset,
      });
      setEntries(page.items);
      setTotal(page.total);
      setNowMs(Date.now());
      setError(null);
    } catch (err) {
      // Clear the rows too: leaving the previous page's entries under an error
      // message invites reading stale data as current.
      setEntries([]);
      setError(describeError(err).message);
    } finally {
      setLoading(false);
    }
  }, [actor, action, objectType, offset]);

  // The IIFE keeps the effect callback synchronous; `load` awaits before any
  // setState, so nothing updates state synchronously in the effect body.
  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  /** Any filter change goes back to page one: page 3 of the previous filter is
   *  usually an empty table under the new one. */
  function refilter(apply: () => void) {
    apply();
    setOffset(0);
  }

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="bg-muted text-muted-foreground flex size-10 items-center justify-center rounded-lg">
          <ScrollText className="size-5" />
        </div>
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Audit Log</h2>
          <p className="text-muted-foreground text-sm">
            Every change made through MegooPM, and who made it.
          </p>
        </div>
      </div>

      <div className="space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <SearchInput
            value={actorQuery}
            onValueChange={(next) => refilter(() => setActorQuery(next))}
            label="Search audit log by actor"
            placeholder="Email, or part of one"
          />
          <div className="space-y-1.5">
            <Label htmlFor="audit-action">Action</Label>
            <Select
              value={action}
              onValueChange={(value) => refilter(() => setAction(value as string))}
              items={{
                [ANY]: "Any action",
                ...Object.fromEntries(ACTIONS.map((a) => [a.value, a.label])),
              }}
            >
              <SelectTrigger id="audit-action" size="sm" className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>Any action</SelectItem>
                {ACTIONS.map((a) => (
                  <SelectItem key={a.value} value={a.value}>
                    {a.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="audit-object">Object type</Label>
            <Select
              value={objectType}
              onValueChange={(value) => refilter(() => setObjectType(value as string))}
              items={{
                [ANY]: "Any object",
                ...Object.fromEntries(OBJECT_TYPES.map((t) => [t, label(t)])),
              }}
            >
              <SelectTrigger id="audit-object" size="sm" className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>Any object</SelectItem>
                {OBJECT_TYPES.map((type) => (
                  <SelectItem key={type} value={type}>
                    {label(type)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {error ? (
          <div className="border-destructive/30 bg-destructive/5 flex flex-col items-start gap-3 rounded-xl border p-4">
            <p className="text-destructive text-sm" role="alert">
              Couldn&apos;t load the audit log: {error}
            </p>
            <Button variant="outline" size="sm" onClick={() => void load()}>
              Retry
            </Button>
          </div>
        ) : null}

        {isMobile ? (
          <div className="bg-card text-card-foreground rounded-xl border shadow-xs">
            {loading ? (
              <div className="space-y-3 p-4">
                <Skeleton className="h-4 w-2/3" />
                <Skeleton className="h-4 w-1/2" />
                <Skeleton className="h-4 w-3/5" />
              </div>
            ) : entries.length === 0 ? (
              <p className="text-muted-foreground px-4 py-10 text-center text-sm">
                {error
                  ? "The audit log could not be loaded."
                  : total === 0 && !actor && action === ANY && objectType === ANY
                    ? "No audit entries yet. Changes you make will appear here."
                    : "No audit entries match these filters."}
              </p>
            ) : (
              <ul className="divide-y">
                {entries.map((entry) => (
                  <li key={entry.id} className="space-y-2 p-4 text-sm">
                    <div className="flex items-start justify-between gap-3">
                      {/* break-words, not break-all: an email wraps where it
                          must, not one character per line. */}
                      <span className="min-w-0 font-medium break-words">
                        {entry.actor ?? <span className="text-muted-foreground">System</span>}
                      </span>
                      <span
                        className="text-muted-foreground shrink-0 text-xs whitespace-nowrap"
                        title={new Date(entry.created_at).toLocaleString()}
                      >
                        {formatRelativeTime(entry.created_at, nowMs)}
                      </span>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="outline" className={TONE_CLASS[actionTone(entry.action)]}>
                        {entry.action}
                      </Badge>
                      <span>{describeObject(entry.object_type, entry.object_id)}</span>
                    </div>
                    <p className="text-muted-foreground text-xs break-words">
                      {summarise(entry.meta)}
                    </p>
                    <div className="flex justify-end">
                      <Button
                        variant="outline"
                        size="sm"
                        aria-label={`Details for entry ${entry.id}`}
                        onClick={() => setSelected(entry)}
                      >
                        Details
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : (
          <div className="bg-card text-card-foreground rounded-xl border shadow-xs">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-28">When</TableHead>
                  <TableHead>Actor</TableHead>
                  <TableHead className="w-24">Action</TableHead>
                  <TableHead>Object</TableHead>
                  <TableHead>Summary</TableHead>
                  <TableHead className="w-24 text-right">Details</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading ? (
                  [0, 1, 2].map((i) => (
                    <TableRow key={i}>
                      {Array.from({ length: 6 }).map((_, c) => (
                        <TableCell key={c}>
                          <Skeleton className="h-4 w-full" />
                        </TableCell>
                      ))}
                    </TableRow>
                  ))
                ) : entries.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={6} className="text-muted-foreground py-10 text-center">
                      {error
                        ? "The audit log could not be loaded."
                        : total === 0 && !actor && action === ANY && objectType === ANY
                          ? "No audit entries yet. Changes you make will appear here."
                          : "No audit entries match these filters."}
                    </TableCell>
                  </TableRow>
                ) : (
                  entries.map((entry) => (
                    <TableRow key={entry.id}>
                      <TableCell
                        className="text-muted-foreground whitespace-nowrap"
                        title={new Date(entry.created_at).toLocaleString()}
                      >
                        {formatRelativeTime(entry.created_at, nowMs)}
                      </TableCell>
                      <TableCell className="font-medium">
                        {entry.actor ?? <span className="text-muted-foreground">System</span>}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className={TONE_CLASS[actionTone(entry.action)]}>
                          {entry.action}
                        </Badge>
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        {describeObject(entry.object_type, entry.object_id)}
                      </TableCell>
                      <TableCell className="text-muted-foreground max-w-md text-xs">
                        {summarise(entry.meta)}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          aria-label={`Details for entry ${entry.id}`}
                          onClick={() => setSelected(entry)}
                        >
                          Details
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        )}

        <div className="flex items-center justify-between gap-3">
          <p className="text-muted-foreground text-sm">
            {total === 0 ? "No entries" : `${total} entries · page ${page} of ${pages}`}
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              aria-label="Previous page"
              disabled={offset === 0 || loading}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              aria-label="Next page"
              disabled={offset + PAGE_SIZE >= total || loading}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
            </Button>
          </div>
        </div>
      </div>

      <AuditDetailsDialog
        entry={selected}
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
      />
    </div>
  );
}
