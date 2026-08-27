import { describe, expect, it } from "vitest";

import {
  displayName,
  isSelf,
  MIN_PASSWORD_LENGTH,
  validateNewPassword,
} from "@/components/users/lib";

describe("displayName", () => {
  it("prefers the full name and falls back to the email", () => {
    expect(displayName({ full_name: "Ada Lovelace", email: "ada@example.com" })).toBe(
      "Ada Lovelace",
    );
    expect(displayName({ full_name: "", email: "ada@example.com" })).toBe("ada@example.com");
    expect(displayName({ full_name: "   ", email: "ada@example.com" })).toBe("ada@example.com");
  });
});

describe("isSelf", () => {
  it("matches on id only", () => {
    expect(isSelf({ id: 1 }, { id: 1 })).toBe(true);
    expect(isSelf({ id: 1 }, { id: 2 })).toBe(false);
    expect(isSelf({ id: 1 }, null)).toBe(false);
    expect(isSelf({ id: 1 }, undefined)).toBe(false);
  });
});

describe("validateNewPassword", () => {
  it("enforces the minimum length", () => {
    expect(validateNewPassword("short", "short")).toBe(
      `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`,
    );
  });

  it("requires the confirmation to match", () => {
    expect(validateNewPassword("longenough", "different")).toBe("Passwords do not match.");
  });

  it("returns null for a valid pair", () => {
    expect(validateNewPassword("longenough", "longenough")).toBeNull();
  });
});
