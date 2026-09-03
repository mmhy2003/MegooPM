import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

let token: string | null = "tok-123";
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(token ? { token } : {}),
}));
vi.mock("@/lib/auth/api", () => ({ resetPassword: vi.fn() }));

import { ApiError } from "@/lib/api/errors";
import { resetPassword } from "@/lib/auth/api";
import { ResetPasswordForm } from "@/components/auth/reset-password-form";

afterEach(() => {
  cleanup();
  vi.mocked(resetPassword).mockReset();
  token = "tok-123";
});

describe("ResetPasswordForm", () => {
  it("sends the token from the URL with the new password", async () => {
    const user = userEvent.setup();
    const reset = vi.mocked(resetPassword).mockResolvedValue(undefined);
    render(<ResetPasswordForm />);

    await user.type(screen.getByLabelText("New password"), "brandnew12345");
    await user.type(screen.getByLabelText("Confirm password"), "brandnew12345");
    await user.click(screen.getByRole("button", { name: /set new password/i }));

    expect(reset).toHaveBeenCalledWith("tok-123", "brandnew12345");
    expect(await screen.findByText(/password has been changed/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /sign in/i })).toHaveAttribute("href", "/login");
  });

  it("refuses mismatched passwords before sending anything", async () => {
    const user = userEvent.setup();
    const reset = vi.mocked(resetPassword);
    render(<ResetPasswordForm />);

    await user.type(screen.getByLabelText("New password"), "brandnew12345");
    await user.type(screen.getByLabelText("Confirm password"), "different12345");
    await user.click(screen.getByRole("button", { name: /set new password/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/match/i);
    expect(reset).not.toHaveBeenCalled();
  });

  it("explains a refused token and offers to start over", async () => {
    const user = userEvent.setup();
    vi.mocked(resetPassword).mockRejectedValue(
      new ApiError(400, "Bad request", { detail: "This link is invalid or has expired." }),
    );
    render(<ResetPasswordForm />);

    await user.type(screen.getByLabelText("New password"), "brandnew12345");
    await user.type(screen.getByLabelText("Confirm password"), "brandnew12345");
    await user.click(screen.getByRole("button", { name: /set new password/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/invalid or has expired/i);
    expect(screen.getByRole("link", { name: /request a new link/i })).toHaveAttribute(
      "href",
      "/forgot-password",
    );
  });

  it("says the link is incomplete when there is no token", () => {
    token = null;
    render(<ResetPasswordForm />);
    expect(screen.getByText(/link is incomplete/i)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /set new password/i }),
    ).not.toBeInTheDocument();
  });
});
