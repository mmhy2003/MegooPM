"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Loader2 } from "lucide-react";

import { backups, type RestoreStatus } from "@/lib/api";
import { Button } from "@/components/ui/button";

/** Fast enough to feel live, slow enough not to hammer the one open route. */
const POLL_MS = 2000;

/**
 * The screen an operator watches while a restore runs.
 *
 * During a restore the API answers 503 to everything but this status route, so
 * there is nothing else the app can usefully show — and a half-rendered page
 * full of failed requests would read as the restore having broken something.
 * When it ends, any sign-in made after the backup was taken is gone with the
 * old database, so the way forward is signing in again.
 */
export function RestoreBlocker() {
  const [status, setStatus] = useState<RestoreStatus | null>(null);
  const [wasRunning, setWasRunning] = useState(false);

  const poll = useCallback(async () => {
    try {
      const next = await backups.restoreStatus();
      setStatus(next);
      if (next.running) setWasRunning(true);
    } catch {
      // A failed poll during a restore is expected — the API is 503 and the
      // server may restart. Keep the screen and try again.
    }
  }, []);

  useEffect(() => {
    void (async () => {
      await poll();
    })();
    const timer = setInterval(() => void poll(), POLL_MS);
    return () => clearInterval(timer);
  }, [poll]);

  const running = status?.running === true;
  const finished = wasRunning && status?.running === false;

  return (
    <div className="bg-background/95 fixed inset-0 z-50 flex items-center justify-center p-6">
      <div className="bg-card text-card-foreground w-full max-w-md space-y-4 rounded-xl border p-6 shadow-lg">
        <h2 className="text-lg font-semibold">
          {running ? "Restoring this instance" : finished ? "Restore finished" : "Restore"}
        </h2>

        {running ? (
          <div className="flex items-center gap-3 text-sm">
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            <span>{status?.step ?? "working"}</span>
          </div>
        ) : null}

        {status?.error ? (
          <p role="alert" className="text-destructive text-sm">
            The restore stopped: {status.error}
          </p>
        ) : null}

        {finished && !status?.error ? (
          <p className="text-sm">
            The database was replaced. If your sign-in is newer than the backup, it is gone — sign
            in again to continue.
          </p>
        ) : null}

        {running ? (
          <p className="text-muted-foreground text-sm">
            The admin interface is unavailable until this finishes. Proxied traffic is unaffected.
          </p>
        ) : (
          <Button render={<Link href="/login">Sign in again</Link>} />
        )}
      </div>
    </div>
  );
}
