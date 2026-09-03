import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/lib/auth/api", () => ({ requestPasswordReset: vi.fn() }));

import { ApiError } from "@/lib/api/errors";
import { requestPasswordReset } from "@/lib/auth/api";
import { ForgotPasswordForm } from "@/components/auth/forgot-password-form";

afterEach(() => {
  cleanup();
  vi.mocked(requestPasswordReset).mockReset();
});

describe("ForgotPasswordForm", () => {
  it("shows the neutral message after a request", async () => {
    const user = userEvent.setup();
    vi.mocked(requestPasswordReset).mockResolvedValue(undefined);
    render(<ForgotPasswordForm />);

    await user.type(screen.getByLabelText("Email"), "me@example.com");
    await user.click(screen.getByRole("button", { name: /send reset link/i }));

    expect(await screen.findByText(/if that address is registered/i)).toBeInTheDocument();
  });

  it("shows the same message whatever the backend decided", async () => {
    // The page must not become a second oracle on top of the API.
    const user = userEvent.setup();
    vi.mocked(requestPasswordReset).mockResolvedValue(undefined);
    render(<ForgotPasswordForm />);

    await user.type(screen.getByLabelText("Email"), "nobody@example.com");
    await user.click(screen.getByRole("button", { name: /send reset link/i }));

    expect(await screen.findByText(/if that address is registered/i)).toBeInTheDocument();
    expect(screen.queryByText(/not found/i)).not.toBeInTheDocument();
  });

  it("says to wait on a rate limit", async () => {
    const user = userEvent.setup();
    vi.mocked(requestPasswordReset).mockRejectedValue(
      new ApiError(429, "Too many requests", { detail: "Too many requests. Try again later." }),
    );
    render(<ForgotPasswordForm />);

    await user.type(screen.getByLabelText("Email"), "me@example.com");
    await user.click(screen.getByRole("button", { name: /send reset link/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/too many/i);
  });

  it("links back to sign in", () => {
    render(<ForgotPasswordForm />);
    expect(screen.getByRole("link", { name: /back to sign in/i })).toHaveAttribute(
      "href",
      "/login",
    );
  });
});
