/**
 * Accounts that have signed in on *this browser*, offered on the login page so
 * a returning user only types a password.
 *
 * Per-browser by necessity, not by preference: an endpoint listing accounts
 * before anyone is authenticated would hand every admin email to whoever can
 * reach the login page, which for an internet-facing proxy manager is
 * everyone. So this can only ever know about sign-ins that happened here.
 *
 * It still leaks on a shared machine — which is why every entry carries a
 * Remove action in the UI. Passwords are never stored, only the address and
 * the display name.
 *
 * Every access is wrapped: private windows and browsers with site data blocked
 * throw on `localStorage` itself, and a login page that white-screens over a
 * convenience is far worse than a login page with no convenience.
 */

/** Storage key holding the list. */
export const RECENT_ACCOUNTS_KEY = "megoopm.recentAccounts";

/** How many accounts are offered before the oldest is dropped. */
export const MAX_RECENT_ACCOUNTS = 5;

/**
 * One remembered account.
 *
 * `email` and `full_name` are named to match `Pick<User, …>` so `displayName()`
 * and `initials()` from the users module render these rows unchanged — the row
 * looks like the avatar in the topbar because it is built by the same code.
 */
export interface RecentAccount {
  email: string;
  full_name: string;
  /** ISO timestamp of the last successful sign-in, for ordering. */
  lastUsedAt: string;
}

function isAccount(value: unknown): value is RecentAccount {
  if (typeof value !== "object" || value === null) return false;
  const row = value as Record<string, unknown>;
  // A half-written entry would render a nameless, emailless row; drop it.
  return typeof row.email === "string" && row.email.length > 0;
}

/** Accounts remembered on this browser, most recently used first. */
export function readAccounts(): RecentAccount[] {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(RECENT_ACCOUNTS_KEY);
  } catch {
    return [];
  }
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isAccount).map((row) => ({
      email: row.email,
      full_name: typeof row.full_name === "string" ? row.full_name : "",
      lastUsedAt: typeof row.lastUsedAt === "string" ? row.lastUsedAt : "",
    }));
  } catch {
    // Hand-edited storage, or a shape from an older version of this module.
    return [];
  }
}

function write(accounts: RecentAccount[]): void {
  try {
    window.localStorage.setItem(RECENT_ACCOUNTS_KEY, JSON.stringify(accounts));
  } catch {
    // Quota exceeded, or storage disabled. Signing in still has to succeed.
  }
}

/**
 * Record a successful sign-in, moving the account to the front.
 *
 * Called only after the credentials were accepted *and* the user was fetched:
 * remembering a failed attempt would offer an address that cannot sign in.
 */
export function rememberAccount(user: { email: string; full_name: string }): void {
  const key = user.email.trim().toLowerCase();
  // The backend authenticates case-insensitively, so two rows for one person
  // would be a shortcut that reads as a bug.
  const rest = readAccounts().filter((a) => a.email.trim().toLowerCase() !== key);
  const next: RecentAccount[] = [
    { email: user.email, full_name: user.full_name, lastUsedAt: new Date().toISOString() },
    ...rest,
  ];
  write(next.slice(0, MAX_RECENT_ACCOUNTS));
}

/** Drop one account — the escape hatch for a shared machine. */
export function forgetAccount(email: string): void {
  const key = email.trim().toLowerCase();
  write(readAccounts().filter((a) => a.email.trim().toLowerCase() !== key));
}
