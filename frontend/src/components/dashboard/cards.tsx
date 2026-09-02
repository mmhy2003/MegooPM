/**
 * The dashboard's stat cards.
 *
 * Each takes only its own group from the summary payload, so a card can be
 * rendered from a fixture with no knowledge of the rest — and so one failing
 * source cannot blank the others.
 *
 * The recurring rule here: **absent is not zero**. A source that has not
 * reported says so, because "0 connections" and "nothing has measured this"
 * mean opposite things to an operator deciding whether to page someone.
 */
import {
  Activity,
  Boxes,
  Server,
  ShieldAlert,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";

import type {
  CertificateHealth,
  ConfigHealth,
  InventoryCounts,
  SecuritySummary,
  TrafficSummary,
} from "@/lib/api";

function Card({
  title,
  icon: Icon,
  children,
}: {
  title: string;
  icon: LucideIcon;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3 rounded-xl border p-4">
      <h3 className="text-muted-foreground flex items-center gap-2 text-sm font-medium">
        {/* Decorative: the title beside it already names the card, so a screen
            reader announcing the icon too would just repeat itself. */}
        <Icon className="size-4 shrink-0" aria-hidden="true" />
        {title}
      </h3>
      {children}
    </section>
  );
}

function Figure({ value, label }: { value: string | number; label: string }) {
  return (
    <div className="space-y-0.5">
      <p className="text-2xl font-semibold tabular-nums">{value}</p>
      <p className="text-muted-foreground text-xs">{label}</p>
    </div>
  );
}

function Absent({ children }: { children: React.ReactNode }) {
  return <p className="text-muted-foreground text-sm">{children}</p>;
}

export function CertificatesCard({
  certificates,
}: {
  certificates: CertificateHealth;
}) {
  const needsAttention =
    certificates.expiring_soon + certificates.expired + certificates.failed;
  return (
    <Card title="Certificates" icon={ShieldCheck}>
      {needsAttention === 0 ? (
        <Absent>All healthy — {certificates.total} in total.</Absent>
      ) : (
        <div className="flex gap-6">
          <Figure
            value={certificates.expiring_soon}
            label="expiring in 30 days"
          />
          <Figure value={certificates.expired} label="expired" />
          <Figure value={certificates.failed} label="failed" />
        </div>
      )}
    </Card>
  );
}

export function ConfigHealthCard({ config }: { config: ConfigHealth }) {
  return (
    <Card title="Config health" icon={Server}>
      <div className="space-y-1">
        <p className="text-2xl font-semibold tabular-nums">
          {config.nodes_in_sync} of {config.nodes_total}
        </p>
        <p className="text-muted-foreground text-xs">
          nodes on config version {config.config_version}
        </p>
        <p className={config.converged ? "text-xs" : "text-warning text-xs"}>
          {config.converged ? "In sync" : "Not converged"}
          {config.nodes_stale > 0
            ? ` · ${config.nodes_stale} not seen recently`
            : ""}
        </p>
      </div>
    </Card>
  );
}

/**
 * Drops the collection prefix from a CrowdSec scenario name.
 *
 * Every scenario from a given collection repeats it — `crowdsecurity/` on all
 * five of them — which pushes the distinguishing half off the end of a badge.
 * The full name stays as the badge's title.
 */
export function shortScenario(scenario: string): string {
  const slash = scenario.lastIndexOf("/");
  return slash === -1 ? scenario : scenario.slice(slash + 1);
}

export function SecurityCard({
  security,
}: {
  security: SecuritySummary | null;
}) {
  return (
    <Card title="Security" icon={ShieldAlert}>
      {security === null ? (
        <Absent>CrowdSec unavailable — no data to show.</Absent>
      ) : (
        <div className="space-y-2">
          <div className="flex gap-6">
            <Figure value={security.active_decisions} label="active bans" />
            <Figure value={security.alerts_24h} label="recent alerts" />
          </div>
          {security.top_scenarios.length > 0 ? (
            <div className="space-y-1.5">
              <p className="text-muted-foreground text-xs">Top scenarios</p>
              <div className="flex flex-wrap gap-1.5">
                {security.top_scenarios.map((scenario) => (
                  <Badge
                    key={scenario}
                    variant="outline"
                    className="font-mono text-xs"
                    // The full name on hover: the collection is dropped for
                    // readability, but two collections can carry the same
                    // scenario name, so it is moved rather than lost.
                    title={scenario}
                  >
                    {shortScenario(scenario)}
                  </Badge>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </Card>
  );
}

export function TrafficCard({ traffic }: { traffic: TrafficSummary }) {
  const measured = traffic.active_connections !== null;
  return (
    <Card title="Live traffic" icon={Activity}>
      {!measured ? (
        <Absent>No data yet — waiting for the first sample.</Absent>
      ) : (
        <div className="space-y-2">
          <div className="flex gap-6">
            <Figure
              value={traffic.active_connections ?? 0}
              label="active connections"
            />
            {/* Labelled as an average on purpose: stub_status reports cumulative
                counters, so this is a delta between scrapes, not a live figure. */}
            <Figure
              value={traffic.requests_per_second ?? 0}
              label="req/s (15s avg)"
            />
          </div>
          {traffic.stale_nodes > 0 ? (
            <p className="text-warning text-xs">
              {traffic.stale_nodes} node{traffic.stale_nodes === 1 ? "" : "s"}{" "}
              not reporting — totals exclude{" "}
              {traffic.stale_nodes === 1 ? "it" : "them"}.
            </p>
          ) : null}
        </div>
      )}
    </Card>
  );
}

export function InventoryCard({ inventory }: { inventory: InventoryCounts }) {
  return (
    <Card title="Inventory" icon={Boxes}>
      <div className="space-y-1">
        <p className="text-2xl font-semibold tabular-nums">
          {inventory.proxy_hosts_enabled} of {inventory.proxy_hosts_total}
        </p>
        <p className="text-muted-foreground text-xs">proxy hosts enabled</p>
        <p className="text-muted-foreground text-xs">
          {inventory.redirection_hosts} redirects · {inventory.dead_hosts} 404
          hosts · {inventory.streams} streams
        </p>
      </div>
    </Card>
  );
}
