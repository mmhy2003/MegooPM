import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { users } from "@/lib/api";
import { ApiError } from "@/lib/api/errors";
import { ProfileView } from "@/components/profile/profile-view";

const refreshUser = vi.fn().mockResolvedValue(undefined);
const logout = vi.fn();
const me = {
  id: 2,
  email: "member@example.com",
  full_name: "Member User",
  role: "member" as const,
  is_active: true,
  created_at: "2026-08-27T09:00:00Z",
  updated_at: "2026-08-27T09:00:00Z",
  totp_enabled: false,
};

vi.mock("@/components/profile/passkeys-card", () => ({ PasskeysCard: () => null }));
vi.mock("@/lib/auth/context", () => ({
  useAuth: () => ({
    user: me,
    status: "authenticated",
    login: vi.fn(),
    logout,
    refreshUser,
  }),
}));

describe("ProfileView", () => {
  beforeEach(() => {
    vi.spyOn(users, "updateMe").mockResolvedValue({
      ...me,
      full_name: "Renamed",
    });
    vi.spyOn(users, "changeMyPassword").mockResolvedValue(undefined);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    refreshUser.mockClear();
    logout.mockClear();
  });

  it("saves the display name and refreshes the session user", async () => {
    const user = userEvent.setup();
    render(<ProfileView />);
    const name = screen.getByLabelText("Full name");
    await user.clear(name);
    await user.type(name, "Renamed");
    await user.click(screen.getByRole("button", { name: "Save name" }));
    await waitFor(() => expect(users.updateMe).toHaveBeenCalledWith({ full_name: "Renamed" }));
    await waitFor(() => expect(refreshUser).toHaveBeenCalled());
  });

  it("blocks a mismatched confirmation without calling the API", async () => {
    const user = userEvent.setup();
    render(<ProfileView />);
    await user.type(screen.getByLabelText("New password"), "brandnew123");
    await user.type(screen.getByLabelText("Confirm new password"), "different1");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Passwords do not match.");
    expect(users.changeMyPassword).not.toHaveBeenCalled();
  });

  it("changes the password without asking for the current one", async () => {
    const user = userEvent.setup();
    render(<ProfileView />);
    expect(screen.queryByLabelText("Current password")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("New password"), "brandnew123");
    await user.type(screen.getByLabelText("Confirm new password"), "brandnew123");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    await waitFor(() =>
      expect(users.changeMyPassword).toHaveBeenCalledWith({
        new_password: "brandnew123",
      }),
    );
  });

  it("surfaces an API error inline", async () => {
    vi.spyOn(users, "changeMyPassword").mockRejectedValueOnce(
      new ApiError(422, "Password must be at least 8 characters", {
        detail: "Password must be at least 8 characters",
      }),
    );
    const user = userEvent.setup();
    render(<ProfileView />);
    await user.type(screen.getByLabelText("New password"), "brandnew123");
    await user.type(screen.getByLabelText("Confirm new password"), "brandnew123");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Password must be at least 8 characters",
    );
  });

  it("signs out from the page header", async () => {
    const user = userEvent.setup();
    render(<ProfileView />);
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(logout).toHaveBeenCalledTimes(1);
  });
});
