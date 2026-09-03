import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { users } from "@/lib/api";
import { UsersView } from "@/components/users/users-view";
import { fetchCapabilities } from "@/lib/auth/api";

const admin = {
  id: 1,
  email: "admin@example.com",
  full_name: "Admin User",
  role: "admin" as const,
  is_active: true,
  created_at: "2026-08-27T09:00:00Z",
  updated_at: "2026-08-27T09:00:00Z",
  totp_enabled: false,
};
const member = {
  ...admin,
  id: 2,
  email: "member@example.com",
  full_name: "Mem Ber",
  role: "member" as const,
  is_active: false,
};

vi.mock("@/lib/auth/api", () => ({ fetchCapabilities: vi.fn() }));
vi.mock("@/components/users/invite-dialog", () => ({
  InviteDialog: ({ open }: { open: boolean }) => (open ? <div>invite-dialog</div> : null),
}));
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
    title,
    onConfirm,
    onDeleted,
  }: {
    open: boolean;
    title: string;
    onConfirm: () => Promise<void>;
    onDeleted: () => void;
  }) =>
    open ? (
      <div>
        {/* The title is rendered so a test can see which copy the view chose. */}
        <span>{title}</span>
        <button
          type="button"
          onClick={() => {
            void onConfirm().then(onDeleted);
          }}
        >
          confirm-delete
        </button>
      </div>
    ) : null,
}));

describe("UsersView", () => {
  beforeEach(() => {
    vi.mocked(fetchCapabilities).mockResolvedValue({ password_reset: false, passkeys: false });
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

describe("UsersView invitations", () => {
  const invited = {
    ...member,
    id: 3,
    email: "pending@example.com",
    full_name: "",
    is_active: false,
    invited_at: "2026-09-03T00:00:00Z",
  };

  beforeEach(() => {
    vi.mocked(fetchCapabilities).mockResolvedValue({ password_reset: true, passkeys: false });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("offers Invite user when an invitation could be sent", async () => {
    vi.spyOn(users, "list").mockResolvedValue([admin]);
    render(<UsersView />);
    expect(await screen.findByRole("button", { name: /invite user/i })).toBeInTheDocument();
  });

  it("hides Invite user when email is not configured", async () => {
    // An admin who can see Invite and then learns nothing can be sent has been
    // misled by the UI.
    vi.mocked(fetchCapabilities).mockResolvedValue({ password_reset: false, passkeys: false });
    vi.spyOn(users, "list").mockResolvedValue([admin]);
    render(<UsersView />);
    await screen.findByText("admin@example.com");
    expect(screen.queryByRole("button", { name: /invite user/i })).not.toBeInTheDocument();
  });

  it("shows an Invited badge and a resend action on an invited row", async () => {
    vi.spyOn(users, "list").mockResolvedValue([admin, invited]);
    render(<UsersView />);
    // An invitee has no name yet, so the email fills the Name cell too; both
    // matches sit in the same row.
    const row = (await screen.findAllByText("pending@example.com"))[0].closest("tr")!;
    expect(within(row).getByText("Invited")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: /resend invitation/i })).toBeInTheDocument();
  });

  it("shows neither on an accepted row", async () => {
    vi.spyOn(users, "list").mockResolvedValue([admin, member]);
    render(<UsersView />);
    const row = (await screen.findByText("member@example.com")).closest("tr")!;
    expect(within(row).queryByText("Invited")).not.toBeInTheDocument();
    expect(
      within(row).queryByRole("button", { name: /resend invitation/i }),
    ).not.toBeInTheDocument();
  });

  it("resend calls the right route", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "list").mockResolvedValue([admin, invited]);
    const resend = vi.spyOn(users, "resendInvite").mockResolvedValue(undefined);
    render(<UsersView />);
    // An invitee has no name yet, so the email fills the Name cell too; both
    // matches sit in the same row.
    const row = (await screen.findAllByText("pending@example.com"))[0].closest("tr")!;

    await user.click(within(row).getByRole("button", { name: /resend invitation/i }));

    await waitFor(() => expect(resend).toHaveBeenCalledWith(3));
  });

  it("calls the delete a withdrawal for an invited row", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "list").mockResolvedValue([admin, invited]);
    render(<UsersView />);
    // An invitee has no name yet, so the email fills the Name cell too; both
    // matches sit in the same row.
    const row = (await screen.findAllByText("pending@example.com"))[0].closest("tr")!;

    await user.click(within(row).getByRole("button", { name: /delete pending@example.com/i }));

    expect(await screen.findByText(/withdraw/i)).toBeInTheDocument();
  });
});

describe("UsersView two-factor", () => {
  const withTotp = {
    ...member,
    id: 4,
    email: "totp@example.com",
    full_name: "Totp User",
    totp_enabled: true,
  };

  beforeEach(() => {
    vi.mocked(fetchCapabilities).mockResolvedValue({ password_reset: false, passkeys: false });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows On for a user with 2FA and Off otherwise", async () => {
    vi.spyOn(users, "list").mockResolvedValue([admin, withTotp]);
    render(<UsersView />);
    const on = (await screen.findByText("totp@example.com")).closest("tr")!;
    const off = screen.getByText("admin@example.com").closest("tr")!;
    expect(within(on).getByText("On")).toBeInTheDocument();
    expect(within(off).getByText("Off")).toBeInTheDocument();
  });

  it("offers Disable 2FA only where it is on", async () => {
    vi.spyOn(users, "list").mockResolvedValue([admin, withTotp]);
    render(<UsersView />);
    const on = (await screen.findByText("totp@example.com")).closest("tr")!;
    const off = screen.getByText("admin@example.com").closest("tr")!;
    expect(
      within(on).getByRole("button", { name: /disable 2fa for totp@example.com/i }),
    ).toBeInTheDocument();
    expect(within(off).queryByRole("button", { name: /disable 2fa/i })).not.toBeInTheDocument();
  });

  it("confirms, then calls the admin route", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "list").mockResolvedValue([admin, withTotp]);
    const disable = vi.spyOn(users, "adminTotpDisable").mockResolvedValue(undefined);
    render(<UsersView />);
    const on = (await screen.findByText("totp@example.com")).closest("tr")!;

    await user.click(within(on).getByRole("button", { name: /disable 2fa for/i }));
    expect(await screen.findByText(/Disable two-factor/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "confirm-delete" }));

    await waitFor(() => expect(disable).toHaveBeenCalledWith(4));
  });
});
