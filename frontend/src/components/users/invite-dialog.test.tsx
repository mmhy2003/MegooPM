import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { users } from "@/lib/api";
import { ApiError } from "@/lib/api/errors";
import { InviteDialog } from "@/components/users/invite-dialog";

const INVITED = {
  id: 9,
  email: "new@example.com",
  full_name: "",
  role: "member" as const,
  is_active: false,
  invited_at: "2026-09-03T00:00:00Z",
  created_at: "2026-09-03T00:00:00Z",
  updated_at: "2026-09-03T00:00:00Z",
  totp_enabled: false,
};

beforeEach(() => {
  vi.spyOn(toast, "success").mockImplementation(() => "" as never);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("InviteDialog", () => {
  it("sends email, name and role, then reports back", async () => {
    const user = userEvent.setup();
    const invite = vi.spyOn(users, "invite").mockResolvedValue(INVITED);
    const onSaved = vi.fn();
    render(<InviteDialog open onOpenChange={() => {}} onSaved={onSaved} />);

    await user.type(screen.getByLabelText("Email"), "new@example.com");
    await user.click(screen.getByRole("button", { name: /send invitation/i }));

    await waitFor(() => expect(invite).toHaveBeenCalled());
    expect(invite.mock.calls[0][0]).toMatchObject({
      email: "new@example.com",
      role: "member",
    });
    expect(onSaved).toHaveBeenCalled();
  });

  it("refuses an empty email before sending", async () => {
    const user = userEvent.setup();
    const invite = vi.spyOn(users, "invite");
    render(<InviteDialog open onOpenChange={() => {}} onSaved={() => {}} />);

    await user.click(screen.getByRole("button", { name: /send invitation/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/email/i);
    expect(invite).not.toHaveBeenCalled();
  });

  it("surfaces the backend's reason for a refusal", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "invite").mockRejectedValue(
      new ApiError(409, "Conflict", {
        detail: "A user with that email already exists",
      }),
    );
    render(<InviteDialog open onOpenChange={() => {}} onSaved={() => {}} />);

    await user.type(screen.getByLabelText("Email"), "taken@example.com");
    await user.click(screen.getByRole("button", { name: /send invitation/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/already exists/i);
  });
});
