"use client";

/**
 * Client-side auth orchestration.
 *
 * Owns the in-memory session state (current user + status) and wires the API
 * client's token seams to the cookie store in {@link module:lib/auth/session}:
 *
 * - `setAuthTokenProvider` — every request attaches the current access token.
 * - `setTokenRefresher` — a 401 triggers one refresh + retry; a failed refresh
 *   tears down the session so the guard bounces the user to `/login`.
 *
 * The provider owns login/logout and, on mount, hydrates the user from the
 * session cookies (surviving a page reload).
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useRouter } from "next/navigation";

import { setAuthTokenProvider, setTokenRefresher } from "@/lib/api/client";
import {
  fetchCurrentUser,
  login as loginRequest,
  refresh as refreshRequest,
  type CurrentUser,
} from "@/lib/auth/api";
import {
  clearSession,
  getAccessToken,
  getRefreshToken,
  hasSession,
  LOGIN_ROUTE,
  persistSession,
} from "@/lib/auth/session";

// Registered at module load so the client attaches the session token even for
// requests that fire before the provider mounts.
setAuthTokenProvider(() => getAccessToken());

export type AuthStatus = "loading" | "authenticated" | "unauthenticated";

export interface AuthContextValue {
  user: CurrentUser | null;
  status: AuthStatus;
  /** Authenticate with credentials; throws `ApiError` on failure. */
  login: (email: string, password: string) => Promise<void>;
  /** Clear the session and return to the login page. */
  logout: () => void;
  /** Re-fetch `/users/me` (e.g. after a profile edit) so the shell reflects it. */
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");

  const endSession = useCallback(() => {
    clearSession();
    setUser(null);
    setStatus("unauthenticated");
  }, []);

  // Wire the 401 -> refresh -> retry seam. Returns the new access token, or
  // null (tearing down the session) when refresh is impossible.
  useEffect(() => {
    setTokenRefresher(async () => {
      const refreshToken = getRefreshToken();
      if (!refreshToken) {
        endSession();
        return null;
      }
      try {
        const tokens = await refreshRequest(refreshToken);
        persistSession(tokens);
        return tokens.access_token;
      } catch {
        endSession();
        return null;
      }
    });
    return () => setTokenRefresher(null);
  }, [endSession]);

  // Hydrate the user from existing session cookies on first mount.
  useEffect(() => {
    let active = true;
    (async () => {
      if (!hasSession()) {
        if (active) setStatus("unauthenticated");
        return;
      }
      try {
        const me = await fetchCurrentUser();
        if (active) {
          setUser(me);
          setStatus("authenticated");
        }
      } catch {
        if (active) endSession();
      }
    })();
    return () => {
      active = false;
    };
  }, [endSession]);

  const login = useCallback(async (email: string, password: string) => {
    const tokens = await loginRequest(email, password);
    persistSession(tokens);
    const me = await fetchCurrentUser();
    setUser(me);
    setStatus("authenticated");
  }, []);

  const logout = useCallback(() => {
    endSession();
    router.replace(LOGIN_ROUTE);
  }, [endSession, router]);

  const refreshUser = useCallback(async () => {
    const me = await fetchCurrentUser();
    setUser(me);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ user, status, login, logout, refreshUser }),
    [user, status, login, logout, refreshUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an <AuthProvider>");
  }
  return ctx;
}
