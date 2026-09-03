"use client";

import { useCallback, useEffect, useState } from "react";

import {
  crowdsec,
  instanceSettings,
  type CrowdSecMaintenance,
  type InstanceSettings,
} from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { BlocklistCard } from "@/components/security/blocklist-card";
import { HubUpdatesCard } from "@/components/security/hub-updates-card";
import { Skeleton } from "@/components/ui/skeleton";

const POLL_MS = 5000;

/** Loads the settings and both job records; polls while a job is running. */
export function UpdatesTab() {
  const [settings, setSettings] = useState<InstanceSettings | null>(null);
  const [maint, setMaint] = useState<CrowdSecMaintenance | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, m] = await Promise.all([instanceSettings.get(), crowdsec.maintenance()]);
      setSettings(s);
      setMaint(m);
      setError(null);
    } catch (err) {
      setError(describeError(err).message);
    }
  }, []);

  useEffect(() => {
    let active = true;
    void (async () => {
      if (active) await load();
    })();
    return () => {
      active = false;
    };
  }, [load]);

  const busy = Boolean(
    maint &&
    (maint.running.hub ||
      maint.running.capi ||
      (maint.hub && !maint.hub.finished_at) ||
      (maint.capi && !maint.capi.finished_at)),
  );
  useEffect(() => {
    if (!busy) return;
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [busy, load]);

  if (error) {
    return (
      <p role="alert" className="text-destructive text-sm">
        {error}
      </p>
    );
  }
  if (!settings || !maint) return <Skeleton className="h-40 w-full" />;

  return (
    <div className="grid gap-4">
      <HubUpdatesCard
        key={settings.updated_at}
        settings={settings}
        run={maint.hub}
        running={maint.running.hub}
        reloadConfigured={maint.reload_configured}
        onSaved={setSettings}
        onQueued={() => void load()}
        onUpdateNow={async () => {
          // The dialog toasts success and failure; this only makes the call.
          await crowdsec.hubUpdateNow();
        }}
      />
      <BlocklistCard
        desired={settings.crowdsec_capi_enabled}
        run={maint.capi}
        running={maint.running.capi}
        reloadConfigured={maint.reload_configured}
        onChanged={() => void load()}
      />
    </div>
  );
}
