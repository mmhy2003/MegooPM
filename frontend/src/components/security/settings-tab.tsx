"use client";

import { useCallback, useEffect, useState } from "react";

import { crowdsec, type CrowdSecWafStatus } from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { WafSettingsCard } from "@/components/security/waf-settings-card";
import { Skeleton } from "@/components/ui/skeleton";

const POLL_MS = 5000;

/** Loads the WAF limit's status; polls while an apply is running. */
export function SettingsTab() {
  const [status, setStatus] = useState<CrowdSecWafStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await crowdsec.waf());
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

  const busy = Boolean(status && (status.running || (status.run && !status.run.finished_at)));
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
  if (!status) return <Skeleton className="h-40 w-full" />;

  return (
    <div className="grid gap-4">
      {/* Keyed on the saved values so a reload after Save resets the form to them. */}
      <WafSettingsCard
        key={`${status.max_body_mb}-${status.body_exceeded_action}`}
        status={status}
        onChanged={() => void load()}
      />
    </div>
  );
}
