"use client";

/**
 * Read-only detail for one decision or one alert.
 *
 * Everything shown here is already in the list response — the tables just have
 * no room for it. An alert's AS name, its coordinates, the decisions it
 * triggered and its full untruncated message are fetched with every page and
 * discarded, and those are exactly what you want when deciding whether a block
 * was right.
 *
 * Its own file rather than more of security-view.tsx, which is past 700 lines
 * and four tabs already: "show the details of one row" is a separate
 * responsibility and separately testable.
 */

import type { Alert, Decision } from "@/lib/api";
import { CountryFlag } from "@/components/ui/country-flag";
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
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{subtitle}</DialogDescription>
        </DialogHeader>
        <dl className="divide-border divide-y">{children}</dl>
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
      <Row label="Decision id">{decision.id ?? undefined}</Row>
      <Row label="Remediation">{decision.type}</Row>
      <Row label="Scope">{decision.scope}</Row>
      <Row label="Duration">{decision.duration}</Row>
      <Row label="Scenario">{decision.scenario ?? undefined}</Row>
      {/* Where the decision came from: a local scenario, the community
          blocklist, or an operator pressing the button. */}
      <Row label="Origin">{decision.origin ?? undefined}</Row>
      <Row label="Country">
        {decision.country ? (
          <span className="inline-flex items-center gap-1.5">
            <CountryFlag country={decision.country} />
            {decision.country}
          </span>
        ) : undefined}
      </Row>
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
  const source = alert.source ?? null;
  const ip = source?.ip ?? source?.value ?? null;
  const hasCoords = source?.latitude != null && source?.longitude != null;
  // LAPI sends `decisions: null` for every decision-less alert — notably all
  // AppSec detections — and the generated type keeps the field optional.
  const decisions = alert.decisions ?? [];

  return (
    <DetailsShell
      title={ip ?? alert.scenario ?? "Alert"}
      subtitle="What CrowdSec saw, and what it did about it."
      onOpenChange={onOpenChange}
    >
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
    </DetailsShell>
  );
}
