import * as React from "react";

const MOBILE_BREAKPOINT = 768;
const QUERY = `(max-width: ${MOBILE_BREAKPOINT - 1}px)`;

function subscribe(callback: () => void): () => void {
  const mql = window.matchMedia(QUERY);
  mql.addEventListener("change", callback);
  return () => mql.removeEventListener("change", callback);
}

function getSnapshot(): boolean {
  return window.matchMedia(QUERY).matches;
}

/**
 * Tracks whether the viewport is below the mobile breakpoint.
 *
 * Uses `useSyncExternalStore` so state derives from the media query rather than
 * a `setState`-in-effect (which the React compiler / lint rules flag). Returns
 * `false` during SSR, matching the previous behavior.
 */
export function useIsMobile(): boolean {
  return React.useSyncExternalStore(
    subscribe,
    getSnapshot,
    () => false,
  );
}
