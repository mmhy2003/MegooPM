import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

import { useCanWrite } from "@/lib/auth/can-write";

const useAuth = vi.hoisted(() => vi.fn());
vi.mock("@/lib/auth/context", () => ({ useAuth }));

describe("useCanWrite", () => {
  it("is true for an admin", () => {
    useAuth.mockReturnValue({ user: { role: "admin" } });
    expect(renderHook(() => useCanWrite()).result.current).toBe(true);
  });

  it("is false for a member", () => {
    useAuth.mockReturnValue({ user: { role: "member" } });
    expect(renderHook(() => useCanWrite()).result.current).toBe(false);
  });

  it("is false while the user is still unknown", () => {
    // Defaulting to true would flash write controls on every page load and
    // let a member click one before the answer arrives.
    useAuth.mockReturnValue({ user: null });
    expect(renderHook(() => useCanWrite()).result.current).toBe(false);
  });
});
