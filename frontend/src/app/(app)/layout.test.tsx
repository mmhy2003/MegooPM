import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import AppLayout from "@/app/(app)/layout";
import { useSidebar } from "@/components/ui/sidebar";
import { SIDEBAR_COOKIE_NAME } from "@/lib/sidebar-state";

const jar = vi.hoisted(() => ({ values: new Map<string, string>() }));
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => {
      const value = jar.values.get(name);
      return value === undefined ? undefined : { name, value };
    },
  }),
}));

// The shell's parts are tested on their own; here only the sidebar's state matters.
vi.mock("@/components/auth-guard", () => ({
  AuthGuard: ({ children }: { children: ReactNode }) => <>{children}</>,
}));
vi.mock("@/components/app-topbar", () => ({ AppTopbar: () => null }));
vi.mock("@/components/nginx-status/nginx-status-banner", () => ({ NginxStatusBanner: () => null }));
vi.mock("@/components/app-sidebar", () => ({
  AppSidebar: function State() {
    return <p>sidebar {useSidebar().state}</p>;
  },
}));

afterEach(() => jar.values.clear());

async function renderLayout() {
  render(await AppLayout({ children: null, params: Promise.resolve({}) } as never));
}

describe("AppLayout", () => {
  it("opens the sidebar collapsed when this browser last collapsed it", async () => {
    jar.values.set(SIDEBAR_COOKIE_NAME, "false");
    await renderLayout();
    expect(screen.getByText("sidebar collapsed")).toBeInTheDocument();
  });

  it("opens the sidebar expanded when this browser last expanded it", async () => {
    jar.values.set(SIDEBAR_COOKIE_NAME, "true");
    await renderLayout();
    expect(screen.getByText("sidebar expanded")).toBeInTheDocument();
  });

  it("opens the sidebar expanded on a browser that never chose", async () => {
    await renderLayout();
    expect(screen.getByText("sidebar expanded")).toBeInTheDocument();
  });
});
