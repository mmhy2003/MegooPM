import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

let token: string | null = "inv-123";
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(token ? { token } : {}),
}));
vi.mock("@/lib/auth/api", () => ({ acceptInvite: vi.fn() }));

import { ApiError } from "@/lib/api/errors";
import { acceptInvite } from "@/lib/auth/api";
import { AcceptInviteForm } from "@/components/auth/accept-invite-form";

afterEach(() => {
  cleanup();
  vi.mocked(acceptInvite).mockReset();
  token = "inv-123";
});

async function fill(user: ReturnType<typeof userEvent.setup>, confirm = "chosen12345") {
  await user.type(screen.getByLabelText("Full name"), "New Person");
  await user.type(screen.getByLabelText("Password"), "chosen12345");
  await user.type(screen.getByLabelText("Confirm password"), confirm);
}

describe("AcceptInviteForm", () => {
  it("sends the token, name and password", async () => {
    const user = userEvent.setup();
    vi.mocked(acceptInvite).mockResolvedValue(undefined);
    render(<AcceptInviteForm />);

    await fill(user);
    await user.click(screen.getByRole("button", { name: /accept invitation/i }));

    expect(acceptInvite).toHaveBeenCalledWith("inv-123", "New Person", "chosen12345");
    expect(await screen.findByText(/account is ready/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /sign in/i })).toHaveAttribute("href", "/login");
  });

  it("refuses mismatched passwords before sending anything", async () => {
    const user = userEvent.setup();
    render(<AcceptInviteForm />);

    await fill(user, "different12345");
    await user.click(screen.getByRole("button", { name: /accept invitation/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/match/i);
    expect(acceptInvite).not.toHaveBeenCalled();
  });

  it("points a refused token at an administrator, not a resend", async () => {
    // There is no self-service resend: the only address to send to is the
    // one the person holding the link already controls.
    const user = userEvent.setup();
    vi.mocked(acceptInvite).mockRejectedValue(
      new ApiError(400, "Bad request", { detail: "This link is invalid or has expired." }),
    );
    render(<AcceptInviteForm />);

    await fill(user);
    await user.click(screen.getByRole("button", { name: /accept invitation/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/administrator/i);
    expect(screen.queryByRole("link", { name: /request a new/i })).not.toBeInTheDocument();
  });

  it("says the link is incomplete when there is no token", () => {
    token = null;
    render(<AcceptInviteForm />);
    expect(screen.getByText(/link is incomplete/i)).toBeInTheDocument();
  });
});
