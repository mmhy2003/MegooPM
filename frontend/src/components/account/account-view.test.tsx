import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { users } from "@/lib/api";
import { ApiError } from "@/lib/api/errors";
import { AccountView } from "@/components/account/account-view";

const refreshUser = vi.fn().mockResolvedValue(undefined);
const me = {
  id: 2,
  email: "member@example.com",
  full_name: "Member User",
  role: "member" as const,
  is_active: true,
  created_at: "2026-08-27T09:00:00Z",
  updated_at: "2026-08-27T09:00:00Z",
};

vi.mock("@/lib/auth/context", () => ({
  useAuth: () => ({
    user: me,
    status: "authenticated",
    login: vi.fn(),
    logout: vi.fn(),
    refreshUser,
  }),
}));

describe("AccountView", () => {
  beforeEach(() => {
    vi.spyOn(users, "updateMe").mockResolvedValue({ ...me, full_name: "Renamed" });
    vi.spyOn(users, "changeMyPassword").mockResolvedValue(undefined);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    refreshUser.mockClear();
  });

  it("saves the display name and refreshes the session user", async () => {
    const user = userEvent.setup();
    render(<AccountView />);
    const name = screen.getByLabelText("Full name");
    await user.clear(name);
    await user.type(name, "Renamed");
    await user.click(screen.getByRole("button", { name: "Save profile" }));
    await waitFor(() => expect(users.updateMe).toHaveBeenCalledWith({ full_name: "Renamed" }));
    await waitFor(() => expect(refreshUser).toHaveBeenCalled());
  });

  it("blocks a mismatched confirmation without calling the API", async () => {
    const user = userEvent.setup();
    render(<AccountView />);
    await user.type(screen.getByLabelText("Current password"), "memberpass123");
    await user.type(screen.getByLabelText("New password"), "brandnew123");
    await user.type(screen.getByLabelText("Confirm new password"), "different1");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Passwords do not match.");
    expect(users.changeMyPassword).not.toHaveBeenCalled();
  });

  it("surfaces a wrong current password from the API", async () => {
    // ApiError(status, message, body) — see src/lib/api/errors.ts.
    vi.spyOn(users, "changeMyPassword").mockRejectedValueOnce(
      new ApiError(400, "Current password is incorrect", {
        detail: "Current password is incorrect",
      }),
    );
    const user = userEvent.setup();
    render(<AccountView />);
    await user.type(screen.getByLabelText("Current password"), "nope");
    await user.type(screen.getByLabelText("New password"), "brandnew123");
    await user.type(screen.getByLabelText("Confirm new password"), "brandnew123");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Current password is incorrect");
  });
});
