"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { RotateCw, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import { nginx, type NginxApplyStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth/context";
import { subscribeToEvents } from "@/lib/events";
import { describeError } from "@/components/proxy-hosts/lib";
import { culpritOf } from "@/components/nginx-status/lib";
import { Button } from "@/components/ui/button";

/**
 * The floor under the event stream, as on the dashboard: a proxy that blocks
 * the stream must not leave an operator believing a failed apply went through.
 */
const POLL_MS = 60_000;

/**
 * A standing warning while nginx is refusing the generated configuration.
 *
 * A failed `nginx -t` is rolled back and nginx keeps serving the previous
 * config, so nothing visibly breaks — the change the operator just saved simply
 * never takes effect. And because the object that broke it stays in the
 * database, every later apply fails the same way. That is a state, not an
 * event, so this is a banner that stays until an apply succeeds rather than a
 * toast that scrolls away. It shows nginx's own words, because MegooPM cannot
 * know better than nginx what is wrong.
 *
 * Admin-only, like the endpoint: members change nothing that is applied, and
 * would only collect 403s.
 */
export function NginxStatusBanner() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [status, setStatus] = useState<NginxApplyStatus | null>(null);
  const [retrying, setRetrying] = useState(false);

  const load = useCallback(async () => {
    try {
      setStatus(await nginx.status());
    } catch {
      // Keep what is shown: a blip in reaching the API says nothing about
      // nginx, and must neither raise the banner nor clear a real one.
    }
  }, []);

  useEffect(() => {
    if (!isAdmin) return;
    void (async () => {
      await load();
    })();
    const timer = setInterval(() => void load(), POLL_MS);
    // Only apply outcomes move this banner; every other event is noise here.
    const unsubscribe = subscribeToEvents((type) => {
      if (type.startsWith("config.")) void load();
    });
    return () => {
      clearInterval(timer);
      unsubscribe();
    };
  }, [isAdmin, load]);

  async function retry() {
    setRetrying(true);
    try {
      await nginx.reload();
      toast.success("Apply requested");
    } catch (err) {
      toast.error(describeError(err).message);
    } finally {
      setRetrying(false);
    }
  }

  // Nothing unless the last recorded apply failed. `null` is a fresh install or
  // one that has not applied since the upgrade — not a failure.
  if (!isAdmin || status?.ok !== false) return null;

  const output = status.output ?? "";
  const culprit = culpritOf(output);
  const at = status.at ? new Date(status.at).toLocaleString() : null;

  return (
    <div
      role="alert"
      className="border-destructive/40 bg-destructive/10 mx-4 mt-4 rounded-lg border p-4 md:mx-6 md:mt-6"
    >
      <div className="flex flex-wrap items-start gap-3">
        <TriangleAlert className="text-destructive mt-0.5 size-5 shrink-0" aria-hidden="true" />
        <div className="min-w-0 flex-1 space-y-2">
          <p className="text-destructive font-semibold">nginx is not applying changes</p>
          <p className="text-sm">
            The last generated configuration failed <code className="font-mono">nginx -t</code> and
            was rolled back{at ? ` (${at})` : ""}. nginx is still serving the previous
            configuration, and every change will keep rolling back until this is fixed.
          </p>
          {culprit ? (
            <p className="text-sm">
              nginx points at{" "}
              <Link href={culprit.href} className="font-medium underline underline-offset-4">
                {culprit.label}
              </Link>
              . Fix or disable it, and the next apply goes through.
            </p>
          ) : null}
          {output ? (
            <pre className="bg-background/60 max-h-48 overflow-auto rounded-md border p-3 font-mono text-xs whitespace-pre-wrap break-words">
              {output}
            </pre>
          ) : null}
        </div>
        <Button variant="outline" size="sm" onClick={() => void retry()} disabled={retrying}>
          <RotateCw aria-hidden="true" />
          Retry
        </Button>
      </div>
    </div>
  );
}
