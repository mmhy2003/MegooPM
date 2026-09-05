import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { AdminOnly } from "@/components/admin-only";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const useAuth = vi.hoisted(() => vi.fn());
vi.mock("@/lib/auth/context", () => ({ useAuth }));

afterEach(() => {
  cleanup();
  push.mockClear();
});

describe("AdminOnly", () => {
  it("renders the page for an admin", () => {
    useAuth.mockReturnValue({ user: { role: "admin" } });
    render(<AdminOnly>secret</AdminOnly>);
    expect(screen.getByText("secret")).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("sends a member back to the dashboard", async () => {
    // The nav does not link here, but typing the URL used to render a page
    // whose every request then failed.
    useAuth.mockReturnValue({ user: { role: "member" } });
    render(<AdminOnly>secret</AdminOnly>);
    await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
    expect(screen.queryByText("secret")).not.toBeInTheDocument();
  });

  it("shows nothing while the role is still unknown", () => {
    // Rendering first would fire the page's admin-only requests on a member's
    // behalf and fill the console with 403s before the redirect lands.
    useAuth.mockReturnValue({ user: null });
    render(<AdminOnly>secret</AdminOnly>);
    expect(screen.queryByText("secret")).not.toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });
});
