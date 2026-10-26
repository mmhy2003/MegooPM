"use client";

import { useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";

import { APP_NAME } from "@/lib/env";
import { Button } from "@/components/ui/button";

/** How often an open tab asks which build the server runs. */
export const CHECK_EVERY_MS = 5 * 60_000;
/**
 * How soon a newer build, seen once, is checked again before the notice
 * shows: during a rolling update an old node may still answer the next one.
 */
export const CONFIRM_AFTER_MS = 30_000;

/**
 * Asks the person to reload once a newer frontend is deployed.
 *
 * The page knows the build it was served by (`deploymentId`, next.config.ts);
 * /version.json says which one the server runs now. Checked when the tab is
 * opened or comes back into view, every few minutes, and after part of the app
 * fails to load. It never reloads by itself: a half-filled form would be lost.
 */
export function UpdateNotice({ reload = () => window.location.reload() }: { reload?: () => void }) {
  // The newer build being offered.
  const [offered, setOffered] = useState<string | null>(null);
  // The build "Later" was clicked for: not offered again, a newer one is.
  const declined = useRef<string | null>(null);

  useEffect(() => {
    const own = process.env.NEXT_DEPLOYMENT_ID;
    // A build without an ID (the dev server) has nothing to compare.
    if (!own) return;
    let active = true;
    // A newer build seen once, waiting for the check that confirms it.
    let seen: string | null = null;
    let confirm: ReturnType<typeof setTimeout> | undefined;

    const check = async () => {
      let id: unknown;
      try {
        const res = await fetch("/version.json", { cache: "no-store" });
        if (!res.ok) return;
        ({ id } = (await res.json()) as { id?: unknown });
      } catch {
        // Down or restarting: the next check will tell.
        return;
      }
      if (!active) return;
      if (typeof id !== "string" || !id || id === own) {
        seen = null;
        return;
      }
      if (seen !== id) {
        seen = id;
        clearTimeout(confirm);
        confirm = setTimeout(() => void check(), CONFIRM_AFTER_MS);
        return;
      }
      if (id !== declined.current) setOffered(id);
    };

    const onVisible = () => {
      if (document.visibilityState === "visible") void check();
    };
    // Next names a script that would not load (its file gone with the old build) this way.
    const onRejection = (event: PromiseRejectionEvent) => {
      if ((event.reason as { name?: unknown } | null)?.name === "ChunkLoadError") void check();
    };

    void check();
    const every = setInterval(() => void check(), CHECK_EVERY_MS);
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      active = false;
      clearTimeout(confirm);
      clearInterval(every);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);

  if (!offered) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed right-4 bottom-4 z-50 w-88 max-w-[calc(100vw-2rem)] rounded-xl border bg-popover p-4 text-popover-foreground shadow-lg"
    >
      <p className="text-sm font-medium">{APP_NAME} was updated</p>
      <p className="mt-0.5 text-sm text-muted-foreground">
        Reload to get the new version. Save anything you&apos;re editing first.
      </p>
      <div className="mt-3 flex justify-end gap-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            declined.current = offered;
            setOffered(null);
          }}
        >
          Later
        </Button>
        <Button size="sm" onClick={reload}>
          <RefreshCw /> Reload
        </Button>
      </div>
    </div>
  );
}
