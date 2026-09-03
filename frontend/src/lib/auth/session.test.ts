import { afterEach, describe, expect, it } from "vitest";

import {
  clearSession,
  getAccessToken,
  getRefreshToken,
  hasSession,
  isAuthEnabled,
  isPublicRoute,
  persistSession,
} from "@/lib/auth/session";

function resetCookies() {
  for (const part of document.cookie.split("; ")) {
    const name = part.split("=")[0];
    if (name) document.cookie = `${name}=; Path=/; Max-Age=0`;
  }
}

describe("session token store", () => {
  afterEach(resetCookies);

  it("persists and reads back a token pair", () => {
    persistSession({
      access_token: "access-abc",
      refresh_token: "refresh-xyz",
      token_type: "bearer",
    });

    expect(getAccessToken()).toBe("access-abc");
    expect(getRefreshToken()).toBe("refresh-xyz");
    expect(hasSession()).toBe(true);
  });

  it("clears the session cookies", () => {
    persistSession({
      access_token: "a",
      refresh_token: "b",
      token_type: "bearer",
    });
    clearSession();

    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
    expect(hasSession()).toBe(false);
  });

  it("round-trips tokens containing URL-special characters", () => {
    persistSession({
      access_token: "aa.bb;cc=dd",
      refresh_token: "ee ff",
      token_type: "bearer",
    });

    expect(getAccessToken()).toBe("aa.bb;cc=dd");
    expect(getRefreshToken()).toBe("ee ff");
  });
});

describe("route helpers", () => {
  it("treats /login and its children as public", () => {
    expect(isPublicRoute("/login")).toBe(true);
    expect(isPublicRoute("/login/reset")).toBe(true);
    expect(isPublicRoute("/proxy-hosts")).toBe(false);
  });

  it("enforces auth unless explicitly disabled", () => {
    const original = process.env.NEXT_PUBLIC_AUTH_ENABLED;
    try {
      delete process.env.NEXT_PUBLIC_AUTH_ENABLED;
      expect(isAuthEnabled()).toBe(true);
      process.env.NEXT_PUBLIC_AUTH_ENABLED = "false";
      expect(isAuthEnabled()).toBe(false);
    } finally {
      if (original === undefined) delete process.env.NEXT_PUBLIC_AUTH_ENABLED;
      else process.env.NEXT_PUBLIC_AUTH_ENABLED = original;
    }
  });
});

describe("public routes for password reset", () => {
  it("lets anonymous visitors reach the forgot-password page", () => {
    // Otherwise the guard bounces them to /login before they can ask.
    expect(isPublicRoute("/forgot-password")).toBe(true);
  });

  it("lets anonymous visitors reach the reset-password page", () => {
    expect(isPublicRoute("/reset-password")).toBe(true);
  });
});
