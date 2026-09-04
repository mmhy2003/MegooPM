"use client";

import { useCallback, useEffect, useState } from "react";
import { LayoutDashboard } from "lucide-react";

import {
  dashboard,
  type DashboardSummary,
  type ThreatPoint,
  type VisitorSummary,
} from "@/lib/api";
import {
  CertificatesCard,
  ConfigHealthCard,
  InventoryCard,
  SecurityCard,
  TrafficCard,
} from "@/components/dashboard/cards";
import { OriginMap } from "@/components/dashboard/origin-map";
import { PanelBoundary } from "@/components/dashboard/panel-boundary";
import { VisitorsCard } from "@/components/dashboard/visitors-card";
import { subscribeToEvents } from "@/lib/events";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * The floor, not the mechanism.
 *
 * Pushed events refresh the page the moment something happens; this is what
 * keeps it correct when the stream is blocked by a proxy, so it must never go
 * to zero. Push is an accelerator here, never a dependency.
 */
const POLL_MS = 60_000;

export function DashboardView() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [threats, setThreats] = useState<ThreatPoint[]>([]);
  const [visitors, setVisitors] = useState<VisitorSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      // Settled, not all: the threat list depends on CrowdSec and the summary
      // does not, so one being unreachable must not blank the other.
      const [summaryResult, threatsResult, visitorsResult] =
        await Promise.allSettled([
          dashboard.summary(),
          dashboard.threats(),
          dashboard.visitors(7),
        ]);
      if (summaryResult.status === "fulfilled") {
        setSummary(summaryResult.value);
        setError(null);
      } else {
        setError("Could not load the dashboard.");
      }
      if (threatsResult.status === "fulfilled") setThreats(threatsResult.value);
      if (visitorsResult.status === "fulfilled")
        setVisitors(visitorsResult.value);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      await load();
    })();
    const timer = setInterval(() => void load(), POLL_MS);
    // Any event means something shown here may have changed. The client
    // refetches rather than trusting a payload, so the type is only a trigger
    // and there is never a second serialisation to drift from the REST one.
    const unsubscribe = subscribeToEvents(() => void load());
    return () => {
      clearInterval(timer);
      // Both cleanups matter: a leaked EventSource holds a connection open per
      // mount, on a page an operator navigates in and out of all day.
      unsubscribe();
    };
  }, [load]);

  if (loading) {
    return (
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-28 rounded-xl" />
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <div className="bg-muted text-muted-foreground flex size-10 items-center justify-center rounded-lg">
          {/* Decorative: the heading beside it already names the page. */}
          <LayoutDashboard className="size-5" aria-hidden="true" />
        </div>
        <div className="flex-1">
          <h2 className="text-xl font-semibold tracking-tight">Dashboard</h2>
          <p className="text-muted-foreground text-sm">
            Instance health, traffic and attack origins.
          </p>
        </div>
      </div>

      {error ? <p className="text-destructive text-sm">{error}</p> : null}

      {summary ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {/* Certificates first: the failure that takes a site down silently. */}
          <CertificatesCard certificates={summary.certificates} />
          <ConfigHealthCard config={summary.config} />
          <SecurityCard security={summary.security} />
          <TrafficCard traffic={summary.traffic} />
          <InventoryCard inventory={summary.inventory} />
        </div>
      ) : null}

      {visitors ? <VisitorsCard visitors={visitors} /> : null}

      {/* `visitors` is null until the first load resolves. */}
      {/* Boundaried: this panel drives a third-party library that touches
          the DOM and listens for resize, so it is the most likely thing
          here to throw — and the least worth losing the page over. */}
      <PanelBoundary title="Request origins">
        <OriginMap threats={threats} traffic={visitors?.countries ?? []} />
      </PanelBoundary>
    </div>
  );
}
