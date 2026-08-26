"use client";

import { useCallback, useEffect, useState } from "react";
import {
  CircleCheck,
  Plus,
  ShieldAlert,
  ShieldX,
  Trash2,
  TriangleAlert,
} from "lucide-react";

import {
  crowdsec,
  type Alert,
  type CrowdSecHealth,
  type Decision,
  type DecisionScope,
  type DecisionType,
} from "@/lib/api";
import {
  alertSourceKey,
  decisionRowKey,
  describeError,
  formatRelativeTime,
} from "@/components/security/lib";
import { BanDialog } from "@/components/security/ban-dialog";
import { SecurityMetrics } from "@/components/security/security-metrics";
import { UnbanDialog } from "@/components/security/unban-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function typeBadgeVariant(type: DecisionType | string) {
  if (type === "ban") return "destructive" as const;
  if (type === "captcha") return "outline" as const;
  return "secondary" as const;
}

function LoadingRows({ cols }: { cols: number }) {
  return (
    <>
      {[0, 1, 2].map((i) => (
        <TableRow key={i}>
          {Array.from({ length: cols }).map((_, c) => (
            <TableCell key={c}>
              <Skeleton className="h-4 w-full" />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  );
}

function HealthBanner({ health }: { health: CrowdSecHealth | null }) {
  if (!health) return null;
  if (!health.configured) {
    return (
      <div className="flex items-start gap-2.5 rounded-xl border border-warning/30 bg-warning/5 p-3 text-sm">
        <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" />
        <div>
          <p className="font-medium">CrowdSec is not configured</p>
          <p className="text-muted-foreground">
            {health.detail ?? "Set the LAPI URL and key so decisions can be enforced."}
          </p>
        </div>
      </div>
    );
  }
  if (!health.reachable) {
    return (
      <div className="flex items-start gap-2.5 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-sm">
        <ShieldX className="mt-0.5 size-4 shrink-0 text-destructive" />
        <div>
          <p className="font-medium">LAPI unreachable</p>
          <p className="text-muted-foreground">
            {health.detail ?? `Can't reach ${health.lapi_url}. Decisions may be stale.`}
          </p>
        </div>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2.5 rounded-xl border border-success/30 bg-success/5 p-3 text-sm">
      <CircleCheck className="size-4 shrink-0 text-success" />
      <span>
        Connected to LAPI at <span className="font-mono text-xs">{health.lapi_url}</span>
      </span>
    </div>
  );
}

export function SecurityView() {
  const [health, setHealth] = useState<CrowdSecHealth | null>(null);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [banOpen, setBanOpen] = useState(false);
  const [banSeed, setBanSeed] = useState<{ value: string; scope: DecisionScope }>({
    value: "",
    scope: "Ip",
  });
  const [unban, setUnban] = useState<Decision | null>(null);

  const load = useCallback(async () => {
    try {
      const [h, d, a] = await Promise.all([
        crowdsec.health(),
        crowdsec.listDecisions(),
        crowdsec.listAlerts(100),
      ]);
      setHealth(h);
      setDecisions(d.items ?? []);
      setAlerts(a.items ?? []);
      setNowMs(Date.now());
      setLoadError(null);
    } catch (err) {
      setLoadError(describeError(err).message);
    } finally {
      setLoading(false);
    }
  }, []);

  const refresh = useCallback(() => {
    setLoading(true);
    void load();
  }, [load]);

  useEffect(() => {
    let active = true;
    void (async () => {
      if (active) await load();
    })();
    return () => {
      active = false;
    };
  }, [load]);

  function openBan(seed?: { value: string; scope: DecisionScope }) {
    setBanSeed(seed ?? { value: "", scope: "Ip" });
    setBanOpen(true);
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <ShieldAlert className="size-5" />
        </div>
        <div className="flex-1">
          <h2 className="text-xl font-semibold tracking-tight">Security</h2>
          <p className="text-sm text-muted-foreground">
            CrowdSec decisions, alerts and metrics. Ban or unban an IP or range manually.
          </p>
        </div>
        <Button size="sm" onClick={() => openBan()}>
          <Plus /> Manual decision
        </Button>
      </div>

      <HealthBanner health={health} />

      {loadError ? (
        <div className="flex flex-col items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
          <p className="text-sm text-destructive" role="alert">
            Couldn’t load CrowdSec data: {loadError}
          </p>
          <Button variant="outline" size="sm" onClick={refresh}>
            Retry
          </Button>
        </div>
      ) : null}

      <SecurityMetrics decisions={decisions} alerts={alerts} nowMs={nowMs} />

      <Tabs defaultValue="decisions">
        <TabsList>
          <TabsTab value="decisions">
            <ShieldX /> Active decisions
          </TabsTab>
          <TabsTab value="alerts">
            <TriangleAlert /> Recent alerts
          </TabsTab>
        </TabsList>

        {/* ---- Decisions ---- */}
        <TabsPanel value="decisions" className="space-y-3">
          <div className="rounded-xl border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Value</TableHead>
                  <TableHead>Scope</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Scenario</TableHead>
                  <TableHead>Origin</TableHead>
                  <TableHead>Duration</TableHead>
                  <TableHead className="w-16 text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading ? (
                  <LoadingRows cols={7} />
                ) : decisions.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={7} className="py-10 text-center text-muted-foreground">
                      No active decisions. The bouncer isn’t enforcing any bans right now.
                    </TableCell>
                  </TableRow>
                ) : (
                  decisions.map((d, i) => (
                    <TableRow key={decisionRowKey(d, i)}>
                      <TableCell className="font-mono text-xs font-medium">{d.value}</TableCell>
                      <TableCell>
                        <Badge variant="outline">{d.scope}</Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant={typeBadgeVariant(d.type)}>{d.type}</Badge>
                      </TableCell>
                      <TableCell className="max-w-48 truncate text-muted-foreground" title={d.scenario ?? ""}>
                        {d.scenario ?? "—"}
                      </TableCell>
                      <TableCell className="text-muted-foreground">{d.origin ?? "—"}</TableCell>
                      <TableCell className="tabular-nums">{d.duration}</TableCell>
                      <TableCell>
                        <div className="flex justify-end">
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label={`Lift decision on ${d.value}`}
                            disabled={d.id == null}
                            title={d.id == null ? "This decision has no id and can't be lifted" : "Lift decision"}
                            onClick={() => setUnban(d)}
                          >
                            <Trash2 />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        </TabsPanel>

        {/* ---- Alerts ---- */}
        <TabsPanel value="alerts" className="space-y-3">
          <div className="rounded-xl border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Source</TableHead>
                  <TableHead>Scenario</TableHead>
                  <TableHead>Message</TableHead>
                  <TableHead className="w-20 text-right">Events</TableHead>
                  <TableHead className="w-28">Started</TableHead>
                  <TableHead className="w-16 text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading ? (
                  <LoadingRows cols={6} />
                ) : alerts.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                      No recent alerts.
                    </TableCell>
                  </TableRow>
                ) : (
                  alerts.map((a, i) => {
                    const source = alertSourceKey(a);
                    return (
                      <TableRow key={a.id ?? `alert-${i}`}>
                        <TableCell className="font-mono text-xs font-medium">
                          {source ?? "—"}
                        </TableCell>
                        <TableCell className="max-w-48 truncate" title={a.scenario ?? ""}>
                          {a.scenario ?? "—"}
                        </TableCell>
                        <TableCell className="max-w-64 truncate text-muted-foreground" title={a.message ?? ""}>
                          {a.message ?? "—"}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{a.events_count ?? "—"}</TableCell>
                        <TableCell className="text-muted-foreground">
                          {formatRelativeTime(a.start_at ?? a.created_at, nowMs)}
                        </TableCell>
                        <TableCell>
                          <div className="flex justify-end">
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              aria-label={source ? `Ban ${source}` : "Ban source"}
                              disabled={!source}
                              title={source ? "Ban this source" : "No source IP to ban"}
                              onClick={() => source && openBan({ value: source, scope: "Ip" })}
                            >
                              <ShieldX />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })
                )}
              </TableBody>
            </Table>
          </div>
        </TabsPanel>
      </Tabs>

      {banOpen ? (
        <BanDialog
          key={`${banSeed.value}:${banSeed.scope}`}
          open
          onOpenChange={setBanOpen}
          initialValue={banSeed.value}
          initialScope={banSeed.scope}
          onSaved={refresh}
        />
      ) : null}
      {unban ? (
        <UnbanDialog
          open
          onOpenChange={(open) => !open && setUnban(null)}
          decision={unban}
          onLifted={refresh}
        />
      ) : null}
    </div>
  );
}
