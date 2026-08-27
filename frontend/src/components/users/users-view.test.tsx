import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { users } from "@/lib/api";
import { UsersView } from "@/components/users/users-view";

const admin = {
  id: 1,
  email: "admin@example.com",
  full_name: "Admin User",
  role: "admin" as const,
  is_active: true,
  created_at: "2026-08-27T09:00:00Z",
  updated_at: "2026-08-27T09:00:00Z",
};
const member = {
  ...admin,
  id: 2,
  email: "member@example.com",
  full_name: "Mem Ber",
  role: "member" as const,
  is_active: false,
};

vi.mock("@/lib/auth/context", () => ({
  useAuth: () => ({
    user: admin,
    status: "authenticated",
    login: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
  }),
}));

// Dialogs own Select/portal machinery irrelevant here; we only check wiring.
vi.mock("@/components/users/user-dialog", () => ({
  UserDialog: ({ open, onSaved }: { open: boolean; onSaved: () => void }) =>
    open ? (
      <button type="button" onClick={onSaved}>
        confirm-save
      </button>
    ) : null,
}));
vi.mock("@/components/users/reset-password-dialog", () => ({
  ResetPasswordDialog: ({ open }: { open: boolean }) => (open ? <div>reset-dialog</div> : null),
}));
vi.mock("@/components/proxy-hosts/confirm-delete-dialog", () => ({
  ConfirmDeleteDialog: ({
    open,
    onConfirm,
    onDeleted,
  }: {
    open: boolean;
    onConfirm: () => Promise<void>;
    onDeleted: () => void;
  }) =>
    open ? (
      <button
        type="button"
        onClick={() => {
          void onConfirm().then(onDeleted);
        }}
      >
        confirm-delete
      </button>
    ) : null,
}));

describe("UsersView", () => {
  beforeEach(() => {
    vi.spyOn(users, "list").mockResolvedValue([admin, member]);
    vi.spyOn(users, "remove").mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("lists users with role and status", async () => {
    render(<UsersView />);
    expect(await screen.findByText("member@example.com")).toBeInTheDocument();
    expect(screen.getByText("Admin User")).toBeInTheDocument();
    expect(screen.getAllByText("Admin").length).toBeGreaterThan(0);
    expect(screen.getByText("Inactive")).toBeInTheDocument();
  });

  it("marks the signed-in user's row and disables its delete action", async () => {
    render(<UsersView />);
    const row = (await screen.findByText("admin@example.com")).closest("tr") as HTMLElement;
    expect(within(row).getByText("You")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Delete admin@example.com" })).toBeDisabled();
    const other = screen.getByText("member@example.com").closest("tr") as HTMLElement;
    expect(within(other).getByRole("button", { name: "Delete member@example.com" })).toBeEnabled();
  });

  it("deletes a user and refetches", async () => {
    const user = userEvent.setup();
    render(<UsersView />);
    const other = (await screen.findByText("member@example.com")).closest("tr") as HTMLElement;
    await user.click(within(other).getByRole("button", { name: "Delete member@example.com" }));
    await user.click(screen.getByRole("button", { name: "confirm-delete" }));
    await waitFor(() => expect(users.remove).toHaveBeenCalledWith(2));
    await waitFor(() => expect(users.list).toHaveBeenCalledTimes(2));
  });

  it("refetches after the create dialog saves", async () => {
    const user = userEvent.setup();
    render(<UsersView />);
    await screen.findByText("member@example.com");
    await user.click(screen.getByRole("button", { name: /new user/i }));
    await user.click(screen.getByRole("button", { name: "confirm-save" }));
    await waitFor(() => expect(users.list).toHaveBeenCalledTimes(2));
  });

  it("shows the load error state", async () => {
    vi.spyOn(users, "list").mockRejectedValueOnce(new Error("boom"));
    render(<UsersView />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });
});
