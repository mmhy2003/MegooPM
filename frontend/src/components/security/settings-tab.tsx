"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { crowdsec, type CrowdSecWafStatus } from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { WafSettingsCard } from "@/components/security/waf-settings-card";
import { Skeleton } from "@/components/ui/skeleton";

const POLL_MS = 5000;
/** How long to keep looking after a Save for the worker to pick it up. */
const WATCH_MS = 60_000;

/** Loads the WAF limit's status; polls while an apply is running or expected. */
export function SettingsTab() {
  const [status, setStatus] = useState<CrowdSecWafStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  // When the last Save was queued. The reload right after it can land before
  // the worker takes the lock, when nothing reads as running yet, so keep
  // polling until a run started after it has finished, or WATCH_MS passes.
  const savedAt = useRef<number | null>(null);
  const [awaiting, setAwaiting] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = await crowdsec.waf();
      setStatus(next);
      setError(null);
      const since = savedAt.current;
      if (since !== null) {
        const run = next.run;
        const pickedUp = Boolean(run?.finished_at && Date.parse(run.started_at) >= since);
        if (pickedUp || Date.now() >= since + WATCH_MS) {
          savedAt.current = null;
          setAwaiting(false);
        }
      }
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
    status && (status.running || (status.run && !status.run.finished_at) || awaiting),
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
  if (!status) return <Skeleton className="h-40 w-full" />;

  return (
    <div className="grid gap-4">
      {/* Keyed on the saved values so a reload after Save resets the form to them. */}
      <WafSettingsCard
        key={`${status.max_body_mb}-${status.body_exceeded_action}`}
        status={status}
        onChanged={() => {
          savedAt.current = Date.now();
          setAwaiting(true);
          void load();
        }}
      />
    </div>
  );
}
