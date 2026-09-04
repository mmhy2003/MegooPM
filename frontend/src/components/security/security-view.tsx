"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CircleCheck,
  LayoutDashboard,
  Plus,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  ShieldX,
  Trash2,
  TriangleAlert,
  Users,
} from "lucide-react";

import {
  crowdsec,
  DEFAULT_PAGE_SIZE,
  type AlertList,
  type CrowdSecHealth,
  type Decision,
  type DecisionList,
  type DecisionScope,
  type DecisionType,
  type Whitelist,
  type WhitelistApplyStatus,
  type WhitelistCreate,
} from "@/lib/api";
import {
  alertSourceKey,
  clampPage,
  decisionRowKey,
  describeError,
  formatRelativeTime,
} from "@/components/security/lib";
import { BanDialog } from "@/components/security/ban-dialog";
import { PaginationControls } from "@/components/security/pagination-controls";
import { SecurityMetrics } from "@/components/security/security-metrics";
import { UnbanDialog } from "@/components/security/unban-dialog";
import { WhitelistDialog } from "@/components/security/whitelist-dialog";
import { WhitelistStatusBanner } from "@/components/security/whitelist-status-banner";
import { UpdatesTab } from "@/components/security/updates-tab";
import { WhitelistsTable } from "@/components/security/whitelists-table";
import { Badge } from "@/components/ui/badge";
import { CountryFlag } from "@/components/ui/country-flag";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { SearchInput } from "@/components/ui/search-input";
import { Skeleton } from "@/components/ui/skeleton";
import { filterBySearch } from "@/lib/search";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/** sessionStorage key persisting the community toggle across reloads (MEG-44). */
const COMMUNITY_KEY = "mego.crowdsec.includeCommunity";

/** Long enough that a typed IP costs one request, short enough to feel live. */
const SEARCH_DEBOUNCE_MS = 300;

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
  if (!health.machine_registered) {
    return (
      <div className="flex items-start gap-2.5 rounded-xl border border-warning/30 bg-warning/5 p-3 text-sm">
        <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" />
        <div>
          <p className="font-medium">Connected to LAPI, but no machine is registered yet</p>
          <p className="text-muted-foreground">
            {health.detail ??
              "Decisions are readable, but alerts and manual bans need the machine login. The backend registers it automatically; check CROWDSEC_REGISTRATION_TOKEN."}
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

/** Inline error banner shown in place of a table body when a list fails to load. */
function TableError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex flex-col items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
      <p className="text-sm text-destructive" role="alert">
        Couldn’t load CrowdSec data: {message}
      </p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

export function SecurityView() {
  const [health, setHealth] = useState<CrowdSecHealth | null>(null);
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  // Bumped by ban/unban to force both lists to refetch without a full reload.
  const [refreshTick, setRefreshTick] = useState(0);

  // Community toggle — shared by both tables, defaults OFF. Hydrated from the
  // session below so the default SSR render (OFF) never mismatches.
  const [includeCommunity, setIncludeCommunity] = useState(false);

  // Independent server-side pagination state per table.
  const [decPage, setDecPage] = useState(1);
  const [decPageSize, setDecPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [decList, setDecList] = useState<DecisionList | null>(null);
  const [decLoading, setDecLoading] = useState(true);
  const [decError, setDecError] = useState<string | null>(null);
  const [decQuery, setDecQuery] = useState("");
  const decSearch = useDebouncedValue(decQuery, SEARCH_DEBOUNCE_MS);

  const [alertPage, setAlertPage] = useState(1);
  const [alertPageSize, setAlertPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [alertList, setAlertList] = useState<AlertList | null>(null);
  const [alertLoading, setAlertLoading] = useState(true);
  const [alertError, setAlertError] = useState<string | null>(null);
  const [alertQuery, setAlertQuery] = useState("");
  const alertSearch = useDebouncedValue(alertQuery, SEARCH_DEBOUNCE_MS);

  const [banOpen, setBanOpen] = useState(false);
  const [banSeed, setBanSeed] = useState<{ value: string; scope: DecisionScope }>({
    value: "",
    scope: "Ip",
  });
  const [unban, setUnban] = useState<Decision | null>(null);

  // Whitelists. `wlStatus` is separate from the rows on purpose: a row can
  // exist while its apply has failed, and the banner is what says so.
  const [whitelists, setWhitelists] = useState<Whitelist[]>([]);
  const [wlStatus, setWlStatus] = useState<WhitelistApplyStatus | null>(null);
  const [wlDialog, setWlDialog] = useState<{ row: Whitelist | null } | null>(null);
  const [wlQuery, setWlQuery] = useState("");

  // Name and expressions: an expression whitelist's name rarely says what it
  // actually matches, so the rule text has to be searchable.
  const visibleWhitelists = useMemo(
    () => filterBySearch(whitelists, wlQuery, (w) => [w.name, ...w.expressions]),
    [whitelists, wlQuery],
  );

  // Restore the persisted toggle once, after mount. SSR renders the OFF default
  // (no `window`), so this post-mount read is the correct place to hydrate a
  // client-only preference without risking a hydration mismatch.
  useEffect(() => {
    try {
      if (window.sessionStorage.getItem(COMMUNITY_KEY) === "1") {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- hydrating a persisted client preference post-mount
        setIncludeCommunity(true);
      }
    } catch {
      // sessionStorage unavailable (privacy mode) — keep the default.
    }
  }, []);

  const changeDecQuery = useCallback((next: string) => {
    setDecQuery(next);
    // Immediately, not after the debounce: filtering while on page 4 otherwise
    // lands past the end of a shorter result set and shows an empty table.
    setDecPage(1);
  }, []);

  const changeAlertQuery = useCallback((next: string) => {
    setAlertQuery(next);
    setAlertPage(1);
  }, []);

  const setCommunity = useCallback((next: boolean) => {
    setIncludeCommunity(next);
    // Widening/narrowing the filter changes totals — both tables go to page 1.
    setDecPage(1);
    setAlertPage(1);
    try {
      window.sessionStorage.setItem(COMMUNITY_KEY, next ? "1" : "0");
    } catch {
      // ignore — persistence is best-effort.
    }
  }, []);

  // Health is filter-independent; load it once (and on manual refresh).
  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const h = await crowdsec.health();
        if (active) setHealth(h);
      } catch {
        // Health never errors server-side; a transport error just leaves the
        // banner hidden rather than blocking the tables.
      }
    })();
    return () => {
      active = false;
    };
  }, [refreshTick]);

  // Decisions page.
  useEffect(() => {
    let active = true;
    void (async () => {
      setDecLoading(true);
      try {
        const list = await crowdsec.listDecisions({
          page: decPage,
          pageSize: decPageSize,
          includeCommunity,
          q: decSearch,
        });
        if (!active) return;
        setDecList(list);
        setNowMs(Date.now());
        setDecError(null);
        // A removed last item can leave us past the end — step back a page.
        const clamped = clampPage(decPage, list.total, decPageSize);
        if (clamped !== decPage) setDecPage(clamped);
      } catch (err) {
        if (active) setDecError(describeError(err).message);
      } finally {
        if (active) setDecLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [decPage, decPageSize, includeCommunity, decSearch, refreshTick]);

  // Alerts page.
  useEffect(() => {
    let active = true;
    void (async () => {
      setAlertLoading(true);
      try {
        const list = await crowdsec.listAlerts({
          page: alertPage,
          pageSize: alertPageSize,
          includeCommunity,
          q: alertSearch,
        });
        if (!active) return;
        setAlertList(list);
        setNowMs(Date.now());
        setAlertError(null);
        const clamped = clampPage(alertPage, list.total, alertPageSize);
        if (clamped !== alertPage) setAlertPage(clamped);
      } catch (err) {
        if (active) setAlertError(describeError(err).message);
      } finally {
        if (active) setAlertLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [alertPage, alertPageSize, includeCommunity, alertSearch, refreshTick]);

  const refresh = useCallback(() => setRefreshTick((t) => t + 1), []);

  function openBan(seed?: { value: string; scope: DecisionScope }) {
    setBanSeed(seed ?? { value: "", scope: "Ip" });
    setBanOpen(true);
  }

  const decisions = decList?.items ?? [];
  const alerts = alertList?.items ?? [];
  const decTotal = decList?.total ?? 0;
  const alertTotal = alertList?.total ?? 0;

  const loadWhitelists = useCallback(async () => {
    try {
      const [rows, status] = await Promise.all([
        crowdsec.listWhitelists(),
        crowdsec.whitelistStatus(),
      ]);
      setWhitelists(rows);
      setWlStatus(status);
    } catch {
      // The banner and table are secondary to decisions/alerts; a failure here
      // must not blank the page the operator opened to read those.
      setWlStatus(null);
    }
  }, []);

  useEffect(() => {
    let active = true;
    void (async () => {
      if (active) await loadWhitelists();
    })();
    return () => {
      active = false;
    };
  }, [loadWhitelists, refreshTick]);

  const toggleWhitelist = useCallback(
    async (row: Whitelist, next: boolean) => {
      await crowdsec.updateWhitelist(row.id, {
        name: row.name,
        kind: row.kind,
        reason: row.reason,
        description: row.description,
        ips: row.ips,
        cidrs: row.cidrs,
        filter: row.filter,
        expressions: row.expressions,
        enabled: next,
      });
      await loadWhitelists();
    },
    [loadWhitelists],
  );

  const deleteWhitelist = useCallback(
    async (row: Whitelist) => {
      await crowdsec.deleteWhitelist(row.id);
      await loadWhitelists();
    },
    [loadWhitelists],
  );

  const saveWhitelist = useCallback(
    async (body: WhitelistCreate) => {
      const editing = wlDialog?.row;
      if (editing) await crowdsec.updateWhitelist(editing.id, body);
      else await crowdsec.createWhitelist(body);
      setWlDialog(null);
      await loadWhitelists();
    },
    [wlDialog, loadWhitelists],
  );

  const retryWhitelistApply = useCallback(async () => {
    await crowdsec.applyWhitelists();
    await loadWhitelists();
  }, [loadWhitelists]);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <ShieldAlert className="size-5" />
        </div>
        <div className="flex-1">
          <h2 className="text-xl font-semibold tracking-tight">Security</h2>
          <p className="text-sm text-muted-foreground">
            CrowdSec decisions, alerts and metrics. Ban or unban an IP or range manually.
          </p>
        </div>
        <div className="flex items-center gap-2 rounded-lg border px-3 py-1.5">
          <Users className="size-4 text-muted-foreground" />
          <Label htmlFor="community-toggle" className="text-sm font-medium">
            Include community
          </Label>
          <Switch
            id="community-toggle"
            checked={includeCommunity}
            onCheckedChange={setCommunity}
            aria-label="Include community and CAPI records"
          />
        </div>
        <Button size="sm" onClick={() => openBan()}>
          <Plus /> Manual decision
        </Button>
      </div>

      {/* Deliberately outside the tabs: this explains why the lists below are
          failing, so it must not hide behind a tab the operator is not on. */}
      <HealthBanner health={health} />

      <Tabs defaultValue="dashboard">
        <TabsList>
          <TabsTab value="dashboard">
            <LayoutDashboard /> Dashboard
          </TabsTab>
          <TabsTab value="decisions">
            <ShieldX /> Active decisions
          </TabsTab>
          <TabsTab value="alerts">
            <TriangleAlert /> Recent alerts
          </TabsTab>
          <TabsTab value="whitelists">
            <ShieldCheck /> Whitelists
          </TabsTab>
          <TabsTab value="updates">
            <RefreshCw /> Updates
          </TabsTab>
        </TabsList>

        {/* ---- Dashboard ---- */}
        <TabsPanel value="dashboard" className="space-y-3">
          <SecurityMetrics
            decisions={decisions}
            alerts={alerts}
            decisionsTotal={decTotal}
            alertsTotal={alertTotal}
            nowMs={nowMs}
          />
        </TabsPanel>

        {/* ---- Decisions ---- */}
        <TabsPanel value="decisions" className="space-y-3">
          {decError ? (
            <TableError message={decError} onRetry={refresh} />
          ) : (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <SearchInput
                  value={decQuery}
                  onValueChange={changeDecQuery}
                  label="Search decisions"
                  placeholder="IP, range or scenario"
                />
              </div>
              <div className="bg-card text-card-foreground rounded-xl border shadow-xs">
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
                    {decLoading ? (
                      <LoadingRows cols={7} />
                    ) : decisions.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={7} className="py-10 text-center text-muted-foreground">
                          {decQuery.trim() ? (
                            <>
                              No decisions match “{decQuery.trim()}”.{" "}
                              <Button
                                variant="link"
                                size="sm"
                                className="h-auto p-0 align-baseline"
                                onClick={() => changeDecQuery("")}
                              >
                                Clear search
                              </Button>
                            </>
                          ) : (
                            <>
                              No active decisions
                              {includeCommunity ? "" : " (community records are hidden)"}. The
                              bouncer isn’t enforcing any matching bans right now.
                            </>
                          )}
                        </TableCell>
                      </TableRow>
                    ) : (
                      decisions.map((d, i) => (
                        <TableRow key={decisionRowKey(d, i)}>
                          <TableCell className="font-mono text-xs font-medium">
                            <span className="inline-flex items-center gap-1.5">
                              <CountryFlag country={d.country} />
                              {d.value}
                            </span>
                          </TableCell>
                          <TableCell>
                            <Badge variant="outline">{d.scope}</Badge>
                          </TableCell>
                          <TableCell>
                            <Badge variant={typeBadgeVariant(d.type)}>{d.type}</Badge>
                          </TableCell>
                          <TableCell
                            className="max-w-48 truncate text-muted-foreground"
                            title={d.scenario ?? ""}
                          >
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
                                title={
                                  d.id == null
                                    ? "This decision has no id and can't be lifted"
                                    : "Lift decision"
                                }
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
              <PaginationControls
                idPrefix="decisions"
                page={decPage}
                pageSize={decPageSize}
                total={decTotal}
                loading={decLoading}
                onPageChange={setDecPage}
                onPageSizeChange={(size) => {
                  setDecPageSize(size);
                  setDecPage(1);
                }}
              />
            </>
          )}
        </TabsPanel>

        {/* ---- Alerts ---- */}
        <TabsPanel value="alerts" className="space-y-3">
          {alertError ? (
            <TableError message={alertError} onRetry={refresh} />
          ) : (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <SearchInput
                  value={alertQuery}
                  onValueChange={changeAlertQuery}
                  label="Search alerts"
                  placeholder="Source IP or scenario"
                />
              </div>
              <div className="bg-card text-card-foreground rounded-xl border shadow-xs">
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
                    {alertLoading ? (
                      <LoadingRows cols={6} />
                    ) : alerts.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                          {alertQuery.trim() ? (
                            <>
                              No alerts match “{alertQuery.trim()}”.{" "}
                              <Button
                                variant="link"
                                size="sm"
                                className="h-auto p-0 align-baseline"
                                onClick={() => changeAlertQuery("")}
                              >
                                Clear search
                              </Button>
                            </>
                          ) : (
                            <>
                              No recent alerts
                              {includeCommunity ? "" : " (community records are hidden)"}.
                            </>
                          )}
                        </TableCell>
                      </TableRow>
                    ) : (
                      alerts.map((a, i) => {
                        const source = alertSourceKey(a);
                        return (
                          <TableRow key={a.id ?? `alert-${i}`}>
                            <TableCell className="font-mono text-xs font-medium">
                              <span className="inline-flex items-center gap-1.5">
                                <CountryFlag country={a.source?.cn} />
                                {source ?? "—"}
                              </span>
                            </TableCell>
                            <TableCell className="max-w-48 truncate" title={a.scenario ?? ""}>
                              {a.scenario ?? "—"}
                            </TableCell>
                            <TableCell
                              className="max-w-64 truncate text-muted-foreground"
                              title={a.message ?? ""}
                            >
                              {a.message ?? "—"}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {a.events_count ?? "—"}
                            </TableCell>
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
              <PaginationControls
                idPrefix="alerts"
                page={alertPage}
                pageSize={alertPageSize}
                total={alertTotal}
                loading={alertLoading}
                onPageChange={setAlertPage}
                onPageSizeChange={(size) => {
                  setAlertPageSize(size);
                  setAlertPage(1);
                }}
              />
            </>
          )}
        </TabsPanel>

        {/* ---- Whitelists ---- */}
        <TabsPanel value="whitelists" className="space-y-3">
          {wlStatus ? (
            <WhitelistStatusBanner status={wlStatus} onRetry={retryWhitelistApply} />
          ) : null}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <SearchInput
              value={wlQuery}
              onValueChange={setWlQuery}
              label="Search whitelists"
              placeholder="Name or expression"
            />
            <Button size="sm" onClick={() => setWlDialog({ row: null })}>
              <Plus /> Add whitelist
            </Button>
          </div>
          <WhitelistsTable
            rows={visibleWhitelists}
            query={wlQuery}
            onClearSearch={() => setWlQuery("")}
            onToggle={toggleWhitelist}
            onEdit={(row) => setWlDialog({ row })}
            onDelete={deleteWhitelist}
          />
        </TabsPanel>

        {/* ---- Updates ---- */}
        <TabsPanel value="updates" className="space-y-3">
          <UpdatesTab />
        </TabsPanel>
      </Tabs>

      {wlDialog ? (
        <WhitelistDialog
          key={wlDialog.row?.id ?? "new"}
          open
          onOpenChange={(open) => !open && setWlDialog(null)}
          whitelist={wlDialog.row}
          onSubmit={saveWhitelist}
        />
      ) : null}
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
