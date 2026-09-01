"use client";

import { useEffect } from "react";
import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";

import { APP_NAME } from "@/lib/env";
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
        <div className="flex flex-col items-center gap-5">
          <div className="relative grid size-28 place-items-center">
            {/*
              Two arcs, not two full rings: leaving three of the four borders
              transparent is what makes a rotating circle read as a sweep
              rather than a static ring. Cyan outward, magenta inward and
              counter-rotating — the theme's two neons, moving against each
              other.

              `motion-reduce:animate-none` — counter-rotation is exactly the
              motion someone who asked their OS to reduce it does not want. The
              arcs stay, and the glow still marks the page as busy.
            */}
            <span
              aria-hidden
              className="absolute inset-0 animate-spin rounded-full border-2 border-primary border-r-transparent border-b-transparent border-l-transparent [animation-duration:1.6s] motion-reduce:animate-none"
            />
            <span
              aria-hidden
              className="absolute inset-2 animate-spin rounded-full border-2 border-ring border-t-transparent border-r-transparent border-l-transparent [animation-direction:reverse] [animation-duration:2.4s] motion-reduce:animate-none"
            />
            <Image
              src="/logo.png"
              alt={`${APP_NAME} logo`}
              width={64}
              height={64}
              priority
              className="size-16 drop-shadow-[0_0_12px_var(--primary)]"
            />
          </div>
          <span className="text-sm font-medium tracking-[0.3em] text-muted-foreground uppercase">
            {APP_NAME}
          </span>
        </div>
        {/*
          The visible text is now a logo, but this stays an aria-live region:
          without a text node it would announce nothing and a screen reader
          user would get silence where they used to hear "Loading".
        */}
        <span className="sr-only">Loading…</span>
      </div>
    );
  }

  return <>{children}</>;
}
