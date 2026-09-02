"use client";

import { useCallback, useEffect, useState } from "react";

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
import { ThreatGlobe } from "@/components/dashboard/threat-globe";
import { VisitorsCard } from "@/components/dashboard/visitors-card";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Polls at the same cadence the nodes sample at. A faster poll would re-read
 * numbers that had not changed, since each node writes once per interval.
 */
const POLL_MS = 15_000;

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
      if (visitorsResult.status === "fulfilled") setVisitors(visitorsResult.value);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      await load();
    })();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
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
      <div>
        <h2 className="text-lg font-semibold">Dashboard</h2>
        <p className="text-muted-foreground text-sm">
          Instance health, traffic and attack origins.
        </p>
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

      <ThreatGlobe points={threats} />
    </div>
  );
}
