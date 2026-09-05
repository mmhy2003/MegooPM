"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth/context";

/**
 * Renders its children only for an admin, and sends anyone else home.
 *
 * The sidebar already omits these pages for a member, but typing the URL
 * rendered a page whose every request then failed, which reads as a broken app
 * rather than a closed door. The API remains the enforcement point; this only
 * decides what is worth rendering.
 */
export function AdminOnly({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const router = useRouter();
  const allowed = user?.role === "admin";

  useEffect(() => {
    if (user && !allowed) router.push("/");
  }, [user, allowed, router]);

  // Nothing until the role is known: rendering first would fire the page's
  // admin-only requests on a member's behalf and fill the console with 403s.
  return allowed ? <>{children}</> : null;
}
