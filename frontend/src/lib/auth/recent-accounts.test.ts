import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  MAX_RECENT_ACCOUNTS,
  RECENT_ACCOUNTS_KEY,
  forgetAccount,
  readAccounts,
  rememberAccount,
} from "@/lib/auth/recent-accounts";

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("readAccounts", () => {
  it("starts empty on a browser that has never signed in", () => {
    expect(readAccounts()).toEqual([]);
  });

  it("treats malformed JSON as empty rather than throwing", () => {
    // Hand-edited storage, or a shape from an older version of this module.
    window.localStorage.setItem(RECENT_ACCOUNTS_KEY, "{not json");
    expect(readAccounts()).toEqual([]);
  });

  it("treats a non-array payload as empty", () => {
    window.localStorage.setItem(RECENT_ACCOUNTS_KEY, '{"email":"a@b.c"}');
    expect(readAccounts()).toEqual([]);
  });

  it("drops entries missing the fields the list renders", () => {
    // A half-written entry would render a nameless, emailless row.
    window.localStorage.setItem(
      RECENT_ACCOUNTS_KEY,
      JSON.stringify([
        { email: "real@example.com", full_name: "Real", lastUsedAt: "2026-09-01T00:00:00Z" },
        { full_name: "No email" },
        { email: 42 },
      ]),
    );
    expect(readAccounts().map((a) => a.email)).toEqual(["real@example.com"]);
  });

  it("survives a localStorage that throws on access", () => {
    // Private windows and blocked site data throw on the getter itself. A login
    // page that white-screens over a shortcut is worse than having no shortcut.
    vi.spyOn(window.localStorage, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(readAccounts()).toEqual([]);
  });
});

describe("rememberAccount", () => {
  it("records an account so the next visit can offer it", () => {
    rememberAccount({ email: "mm@example.com", full_name: "Mohamed Hammad" });

    expect(readAccounts()).toEqual([
      expect.objectContaining({ email: "mm@example.com", full_name: "Mohamed Hammad" }),
    ]);
  });

  it("puts the most recently used account first", () => {
    rememberAccount({ email: "first@example.com", full_name: "First" });
    rememberAccount({ email: "second@example.com", full_name: "Second" });

    expect(readAccounts().map((a) => a.email)).toEqual([
      "second@example.com",
      "first@example.com",
    ]);
  });

  it("moves a returning account to the top instead of adding a twin", () => {
    rememberAccount({ email: "mm@example.com", full_name: "Mohamed" });
    rememberAccount({ email: "other@example.com", full_name: "Other" });
    rememberAccount({ email: "mm@example.com", full_name: "Mohamed" });

    expect(readAccounts().map((a) => a.email)).toEqual([
      "mm@example.com",
      "other@example.com",
    ]);
  });

  it("treats a differently-cased email as the same account", () => {
    // The backend authenticates case-insensitively; two rows for one person
    // would be a shortcut that looks like a bug.
    rememberAccount({ email: "MM@Example.com", full_name: "Mohamed" });
    rememberAccount({ email: "mm@example.com", full_name: "Mohamed" });

    expect(readAccounts()).toHaveLength(1);
  });

  it("keeps the newest name when an account signs in again", () => {
    rememberAccount({ email: "mm@example.com", full_name: "Old Name" });
    rememberAccount({ email: "mm@example.com", full_name: "New Name" });

    expect(readAccounts()[0].full_name).toBe("New Name");
  });

  it("keeps only the most recent few, dropping the oldest", () => {
    for (let i = 1; i <= MAX_RECENT_ACCOUNTS + 2; i++) {
      rememberAccount({ email: `user${i}@example.com`, full_name: `User ${i}` });
    }

    const stored = readAccounts();
    expect(stored).toHaveLength(MAX_RECENT_ACCOUNTS);
    expect(stored[0].email).toBe(`user${MAX_RECENT_ACCOUNTS + 2}@example.com`);
    expect(stored.map((a) => a.email)).not.toContain("user1@example.com");
  });

  it("survives a localStorage that refuses to write", () => {
    // Quota exceeded, or storage disabled. Signing in must still succeed.
    vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });

    expect(() =>
      rememberAccount({ email: "mm@example.com", full_name: "Mohamed" }),
    ).not.toThrow();
  });
});

describe("forgetAccount", () => {
  it("removes the named account and leaves the rest", () => {
    rememberAccount({ email: "keep@example.com", full_name: "Keep" });
    rememberAccount({ email: "drop@example.com", full_name: "Drop" });

    forgetAccount("drop@example.com");

    expect(readAccounts().map((a) => a.email)).toEqual(["keep@example.com"]);
  });

  it("matches regardless of case, like remembering does", () => {
    rememberAccount({ email: "mm@example.com", full_name: "Mohamed" });

    forgetAccount("MM@EXAMPLE.COM");

    expect(readAccounts()).toEqual([]);
  });

  it("is a no-op for an account that was never stored", () => {
    rememberAccount({ email: "keep@example.com", full_name: "Keep" });

    forgetAccount("stranger@example.com");

    expect(readAccounts()).toHaveLength(1);
  });
});

describe("what the login flow must not remember", () => {
  it("stores no password field under any key", () => {
    // A password in localStorage would survive the session, be readable by any
    // script on the origin, and defeat the point of hashing it server-side.
    rememberAccount({ email: "mm@example.com", full_name: "Mohamed" });

    const raw = window.localStorage.getItem(RECENT_ACCOUNTS_KEY) ?? "";
    expect(raw.toLowerCase()).not.toContain("password");
    expect(Object.keys(readAccounts()[0])).toEqual(["email", "full_name", "lastUsedAt"]);
  });
});
