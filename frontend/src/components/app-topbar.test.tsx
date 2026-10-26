import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { AppTopbar } from "@/components/app-topbar";

vi.mock("next/navigation", () => ({
  usePathname: () => "/profile",
}));
vi.mock("@/lib/auth/context", () => ({
  useAuth: () => ({
    user: {
      id: 1,
      email: "admin@example.com",
      full_name: "Mohamed Hammad",
      role: "admin",
      is_active: true,
      created_at: "2026-08-27T09:00:00Z",
      updated_at: "2026-08-27T09:00:00Z",
    },
    status: "authenticated",
    login: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
  }),
}));
// The sidebar trigger needs the SidebarProvider context; stub it out here.
vi.mock("@/components/ui/sidebar", () => ({
  SidebarTrigger: ({ className }: { className?: string }) => (
    <button type="button" className={className}>
      toggle-sidebar
    </button>
  ),
}));
vi.mock("@/components/mode-toggle", () => ({
  ModeToggle: () => <div>mode-toggle</div>,
}));

describe("AppTopbar", () => {
  afterEach(() => cleanup());

  it("keeps its sidebar toggle for phones only; desktop toggles from the sidebar", () => {
    render(<AppTopbar />);
    // On phones the sidebar is a hidden drawer, so this is the only way to open it.
    expect(screen.getByRole("button", { name: "toggle-sidebar" })).toHaveClass("md:hidden");
  });

  it("shows the user's initials as a link to the profile page", () => {
    render(<AppTopbar />);
    const link = screen.getByRole("link", { name: "Profile" });
    expect(link).toHaveAttribute("href", "/profile");
    expect(link).toHaveAttribute("title", "Mohamed Hammad");
    expect(link).toHaveTextContent("MH");
  });

  it("titles utility routes", () => {
    render(<AppTopbar />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Profile");
  });
});
