"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth/context";
import { LOGIN_ROUTE, REDIRECT_PARAM } from "@/lib/auth/session";

/**
 * Client-side gate for the authenticated app shell.
 *
 * The `proxy` guard is the primary defence (it runs before the request
 * completes), but it can only make an optimistic cookie check. This guard
 * covers the cases the proxy cannot: a session that was torn down client-side
 * (failed refresh, logout) or cookies present but rejected by the backend. It
 * redirects to `/login`, preserving the intended destination, and holds back
 * the shell until the session state is known.
 */
export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (status === "unauthenticated") {
      const params = new URLSearchParams({ [REDIRECT_PARAM]: pathname });
      router.replace(`${LOGIN_ROUTE}?${params.toString()}`);
    }
  }, [status, pathname, router]);

  if (status !== "authenticated") {
    return (
      <div
        className="flex min-h-dvh items-center justify-center"
        role="status"
        aria-live="polite"
      >
        <span className="text-muted-foreground text-sm">Loading…</span>
      </div>
    );
  }

  return <>{children}</>;
}
