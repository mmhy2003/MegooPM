"use client";

/**
 * The whole of one audit row.
 *
 * The table's summary is lossy on purpose — nested objects collapse, long text
 * is cut — so this is where nothing is hidden. It ends with the raw `meta`,
 * because a key the summariser has never seen is exactly the one an
 * investigation needs, and an audit log that shows only what it understands is
 * not a record.
 */

import type { AuditLogEntry } from "@/lib/api";
import { describeObject } from "@/components/audit-log/lib";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/** One label/value pair. An absent value is an em dash, never a blank cell. */
function Row({ label, children }: { label: string; children?: React.ReactNode }) {
  const empty = children == null || children === "";
  return (
    <div className="grid grid-cols-[7rem_1fr] gap-2 py-1">
      <dt className="text-muted-foreground text-sm">{label}</dt>
      <dd className="text-sm break-words">
        {empty ? <span className="text-muted-foreground">—</span> : children}
      </dd>
    </div>
  );
}

export function AuditDetailsDialog({
  entry,
  open,
  onOpenChange,
}: {
  entry: AuditLogEntry | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Audit entry</DialogTitle>
          <DialogDescription>
            The complete record, exactly as it was written. Audit rows are never edited.
          </DialogDescription>
        </DialogHeader>

        {entry ? (
          <div className="space-y-4">
            <dl className="divide-y">
              <Row label="When">{new Date(entry.created_at).toLocaleString()}</Row>
              <Row label="Actor">
                {entry.actor ?? <span className="text-muted-foreground">System</span>}
              </Row>
              <Row label="Action">{entry.action}</Row>
              <Row label="Object">{describeObject(entry.object_type, entry.object_id)}</Row>
              <Row label="Entry id">{entry.id}</Row>
            </dl>

            <div className="space-y-1.5">
              <p className="text-sm font-medium">Details</p>
              <pre className="bg-muted max-h-80 overflow-auto rounded-lg border p-3 font-mono text-xs">
                {JSON.stringify(entry.meta, null, 2)}
              </pre>
            </div>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
