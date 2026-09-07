"use client";

/**
 * Read-only detail for one decision or one alert.
 *
 * The alert half is the browser's `cscli alerts inspect -d`: it opens with
 * what the row already carried, then fetches the rest — the machine that
 * raised it, the Context table the scenario matched on, and the parsed fields
 * of each event behind it. Those live behind a per-alert fetch for the reason
 * cscli does the same: an alert holds tens of events with ~17 fields each, so
 * carrying them in the list would cost every page load for detail no table
 * renders.
 *
 * Its own file rather than more of security-view.tsx, which is past 700 lines
 * and four tabs already: "show the details of one row" is a separate
 * responsibility and separately testable.
 */

import { useEffect, useState } from "react";

import { crowdsec, type Alert, type Decision } from "@/lib/api";
import { describeError } from "@/components/settings/lib";
import { CountryFlag } from "@/components/ui/country-flag";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";

/** One label/value pair. An absent value is an em dash, never a blank cell. */
function Row({ label, children }: { label: string; children?: React.ReactNode }) {
  const empty = children == null || children === "";
  return (
    <div className="grid grid-cols-[9rem_1fr] gap-2 py-1">
      <dt className="text-muted-foreground text-sm">{label}</dt>
      <dd className="text-sm break-words">
        {empty ? <span className="text-muted-foreground">—</span> : children}
      </dd>
    </div>
  );
}

/** An absolute timestamp. The tables show "3h ago"; here you want the clock. */
function absolute(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? iso : at.toLocaleString();
}

/** The key/value tables cscli prints for Context and for each event. */
function PairTable({ pairs }: { pairs: { key?: string | null; value?: string | null }[] }) {
  return (
    <table className="w-full text-sm">
      <tbody>
        {pairs.map((pair, i) => (
          <tr key={`${pair.key}-${i}`} className="align-top">
            <td className="text-muted-foreground w-48 py-0.5 pr-3 font-mono text-xs">{pair.key}</td>
            <td className="py-0.5 break-all">{pair.value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function DetailsShell({
  title,
  subtitle,
  onOpenChange,
  children,
}: {
  title: React.ReactNode;
  subtitle: string;
  onOpenChange: (open: boolean) => void;
  children: React.ReactNode;
}) {
  // Open while mounted: the page renders it only when a row is selected, the
  // same way it renders its unban confirmation.
  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent size="lg" className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{subtitle}</DialogDescription>
        </DialogHeader>
        {children}
      </DialogContent>
    </Dialog>
  );
}

export function DecisionDetailsDialog({
  decision,
  onOpenChange,
}: {
  decision: Decision;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <DetailsShell
      title={decision.value}
      subtitle="What the bouncer is enforcing, and why."
      onOpenChange={onOpenChange}
    >
      <dl className="divide-border divide-y">
        <Row label="Decision id">{decision.id ?? undefined}</Row>
        <Row label="Remediation">{decision.type}</Row>
        <Row label="Scope">{decision.scope}</Row>
        <Row label="Duration">{decision.duration}</Row>
        {/* A duration alone cannot answer "when does this lift?" hours later. */}
        {decision.until ? <Row label="Expires">{absolute(decision.until)}</Row> : null}
        <Row label="Scenario">{decision.scenario ?? undefined}</Row>
        {/* Where it came from: a local scenario, the community blocklist, or
            an operator pressing the button. */}
        <Row label="Origin">{decision.origin ?? undefined}</Row>
        {decision.simulated ? <Row label="Simulated">Recorded but not enforced</Row> : null}
        <Row label="Country">
          {decision.country ? (
            <span className="inline-flex items-center gap-1.5">
              <CountryFlag country={decision.country} />
              {decision.country}
            </span>
          ) : undefined}
        </Row>
      </dl>
    </DetailsShell>
  );
}

export function AlertDetailsDialog({
  alert,
  onOpenChange,
}: {
  alert: Alert;
  onOpenChange: (open: boolean) => void;
}) {
  const [inspected, setInspected] = useState<Alert | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const id = alert.id;
    // An alert LAPI gave no id to cannot be fetched; the row's own fields are
    // all there will ever be.
    if (id == null) return;
    let active = true;
    void (async () => {
      try {
        const full = await crowdsec.getAlert(id);
        if (active) setInspected(full);
      } catch (err) {
        if (active) setError(describeError(err).message);
      }
    })();
    return () => {
      active = false;
    };
  }, [alert.id]);

  // Everything above the fold comes from the row, so the dialog is useful
  // immediately and stays useful if the fetch never lands.
  const source = alert.source ?? null;
  const ip = source?.ip ?? source?.value ?? null;
  const hasCoords = source?.latitude != null && source?.longitude != null;
  // LAPI sends `decisions: null` for every decision-less alert — notably all
  // AppSec detections — and the generated type keeps the field optional.
  const decisions = alert.decisions ?? [];
  const meta = inspected?.meta ?? [];
  const events = inspected?.events ?? [];

  return (
    <DetailsShell
      title={ip ?? alert.scenario ?? "Alert"}
      subtitle="What CrowdSec saw, and what it did about it."
      onOpenChange={onOpenChange}
    >
      <dl className="divide-border divide-y">
        <Row label="Alert id">{alert.id ?? undefined}</Row>
        <Row label="Scenario">{alert.scenario ?? undefined}</Row>
        {/* Untruncated: the table clips this to one line. */}
        <Row label="Message">{alert.message ?? undefined}</Row>
        <Row label="Events">{alert.events_count ?? undefined}</Row>
        <Row label="Source">
          {ip ? (
            <span className="inline-flex items-center gap-1.5">
              <CountryFlag country={source?.cn} />
              {ip}
            </span>
          ) : undefined}
        </Row>
        <Row label="Network">{source?.as_name ?? undefined}</Row>
        {hasCoords ? (
          <Row label="Coordinates">
            {source?.latitude}, {source?.longitude}
          </Row>
        ) : null}
        <Row label="First seen">{absolute(alert.start_at ?? alert.created_at) ?? undefined}</Row>
        <Row label="Last seen">{absolute(alert.stop_at) ?? undefined}</Row>
        {inspected?.machine_id ? <Row label="Machine">{inspected.machine_id}</Row> : null}
        {inspected?.uuid ? (
          <Row label="UUID">
            <span className="font-mono text-xs">{inspected.uuid}</span>
          </Row>
        ) : null}
        {inspected?.simulated ? <Row label="Simulated">Recorded but not enforced</Row> : null}
        <Row label="Decisions">
          {decisions.length === 0 ? (
            // Common and not a fault: every AppSec detection raises an alert
            // without a decision.
            <span className="text-muted-foreground">No decisions were taken.</span>
          ) : (
            <ul className="space-y-0.5">
              {decisions.map((d, i) => (
                <li key={d.id ?? `${d.value}-${i}`}>
                  {d.type} · {d.scope} {d.value} · {d.duration}
                </li>
              ))}
            </ul>
          )}
        </Row>
      </dl>

      {error ? (
        // Scoped to this region: losing LAPI must not cost the header above.
        <p role="alert" className="text-destructive mt-3 text-sm">
          {error}
        </p>
      ) : inspected === null && alert.id != null ? (
        <Skeleton className="mt-3 h-24 w-full" />
      ) : (
        <>
          {meta.length > 0 ? (
            <section className="mt-4">
              {/* CrowdSec's "alert context": what the scenario matched on. */}
              <h3 className="mb-1 text-sm font-medium">Context</h3>
              <PairTable pairs={meta} />
            </section>
          ) : null}

          {events.length > 0 ? (
            <section className="mt-4">
              <h3 className="mb-1 text-sm font-medium">
                Events <span className="text-muted-foreground">({events.length})</span>
              </h3>
              <div className="divide-border max-h-80 divide-y overflow-y-auto rounded-lg border p-2">
                {events.map((event, i) => (
                  <div key={`${event.timestamp}-${i}`} className="py-2 first:pt-0 last:pb-0">
                    <p className="text-muted-foreground mb-1 text-xs">
                      {absolute(event.timestamp) ?? "—"}
                    </p>
                    <PairTable pairs={event.meta ?? []} />
                  </div>
                ))}
              </div>
            </section>
          ) : null}
        </>
      )}
    </DetailsShell>
  );
}
