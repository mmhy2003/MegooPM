/**
 * Live end-to-end check of the auth wiring against a running backend.
 *
 * Skipped unless `RUN_AUTH_E2E=1` and the backend is reachable, so the normal
 * `vitest run` stays hermetic. Exercises the *shipped* client + auth modules:
 *
 *   RUN_AUTH_E2E=1 NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 \
 *   AUTH_E2E_EMAIL=admin@example.com AUTH_E2E_PASSWORD=s3cretpass \
 *     npx vitest run src/lib/auth/auth.e2e.test.ts
 */
import { describe, expect, it } from "vitest";

import { setAuthTokenProvider, setTokenRefresher } from "@/lib/api/client";
import { fetchCurrentUser, isMfaRequired, login, refresh } from "@/lib/auth/api";

const enabled = process.env.RUN_AUTH_E2E === "1";
const email = process.env.AUTH_E2E_EMAIL ?? "admin@example.com";
const password = process.env.AUTH_E2E_PASSWORD ?? "s3cretpass";

describe.skipIf(!enabled)("auth end-to-end (live backend)", () => {
  it("logs in, reads the current user, refreshes, and auto-retries a 401", async () => {
    // 1. Bad credentials -> 401 (form surfaces "Incorrect email or password").
    await expect(login(email, "definitely-wrong")).rejects.toMatchObject({
      status: 401,
    });

    // 2. Login yields a token pair.
    const result = await login(email, password);
    if (isMfaRequired(result)) throw new Error("the e2e account must not have 2FA on");
    const tokens = result;
    expect(tokens.access_token).toBeTruthy();
    expect(tokens.refresh_token).toBeTruthy();

    // 3. Bearer token reaches /users/me.
    let access = tokens.access_token;
    setAuthTokenProvider(() => access);
    const me = await fetchCurrentUser();
    expect(me.email).toBe(email);

    // 4. Refresh issues a fresh, working access token.
    const refreshed = await refresh(tokens.refresh_token);
    expect(refreshed.access_token).toBeTruthy();

    // 5. A stale token 401s, the refresher swaps it in, and the retry succeeds
    //    transparently — the real seam the app relies on.
    access = "not.a.valid.token";
    setTokenRefresher(async () => {
      access = refreshed.access_token;
      return access;
    });
    const meAfterRetry = await fetchCurrentUser();
    expect(meAfterRetry.email).toBe(email);

    setAuthTokenProvider(() => null);
    setTokenRefresher(null);
  });
});
