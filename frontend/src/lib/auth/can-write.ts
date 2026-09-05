"use client";

import { useAuth } from "@/lib/auth/context";

/**
 * Whether the signed-in user may change anything.
 *
 * A convenience over the API, never a substitute for it: every write is
 * refused by `require_admin` regardless of what the UI renders. This exists so
 * a member is not offered buttons that will 403.
 *
 * False while the user is unknown. Defaulting to true would flash write
 * controls on every page load and let a member click one before the answer
 * arrives.
 */
export function useCanWrite(): boolean {
  return useAuth().user?.role === "admin";
}
