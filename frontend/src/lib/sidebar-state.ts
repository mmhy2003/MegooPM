/**
 * Where the browser keeps whether the sidebar is collapsed. The sidebar writes
 * it on every toggle (components/ui/sidebar.tsx); the app layout reads it on
 * the server, so a page opens the way it was left with no flash of the other
 * state. A plain module, not the client sidebar's: a server component that
 * imported a value from a "use client" file would get a reference, not it.
 */
export const SIDEBAR_COOKIE_NAME = "sidebar_state";
/** A year: a preference, not a session. */
export const SIDEBAR_COOKIE_MAX_AGE = 60 * 60 * 24 * 365;
