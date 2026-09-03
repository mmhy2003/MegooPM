import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

// Hoisted: vi.mock is lifted above every import, so a plain const would
// not exist yet when the factory runs.
const { startRegistration } = vi.hoisted(() => ({ startRegistration: vi.fn() }));
vi.mock("@simplewebauthn/browser", () => ({ startRegistration }));
vi.mock("@/lib/auth/api", () => ({ fetchCapabilities: vi.fn() }));

import { users } from "@/lib/api";
import { ApiError } from "@/lib/api/errors";
import { fetchCapabilities } from "@/lib/auth/api";
import { PasskeysCard } from "@/components/profile/passkeys-card";

const ONE = { id: 1, name: "MacBook", created_at: "2026-09-01T09:00:00Z", last_used_at: null };
const OPTIONS = { nonce: "n1", options: { challenge: "abc", rp: { id: "localhost" } } };

beforeEach(() => {
  vi.mocked(fetchCapabilities).mockResolvedValue({ password_reset: false, passkeys: true });
  vi.spyOn(users, "passkeys").mockResolvedValue([ONE]);
  vi.spyOn(toast, "success").mockImplementation(() => "" as never);
  startRegistration.mockReset().mockResolvedValue({ id: "cred", type: "public-key" });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("PasskeysCard visibility", () => {
  it("renders nothing when 2FA is off", async () => {
    render(<PasskeysCard enabled={false} />);
    await waitFor(() => expect(fetchCapabilities).toHaveBeenCalled());
    expect(screen.queryByText(/passkeys/i)).not.toBeInTheDocument();
  });

  it("renders nothing when the app URL is not set", async () => {
    vi.mocked(fetchCapabilities).mockResolvedValue({ password_reset: false, passkeys: false });
    render(<PasskeysCard enabled />);
    await waitFor(() => expect(fetchCapabilities).toHaveBeenCalled());
    expect(screen.queryByText(/passkeys/i)).not.toBeInTheDocument();
  });

  it("lists passkeys with their dates", async () => {
    render(<PasskeysCard enabled />);
    expect(await screen.findByText("MacBook")).toBeInTheDocument();
    expect(screen.getByText(/never used/i)).toBeInTheDocument();
  });
});

describe("PasskeysCard adding", () => {
  it("asks for a code and a name, runs the ceremony, and posts the credential", async () => {
    const user = userEvent.setup();
    const phone = { ...ONE, id: 2, name: "Phone" };
    vi.spyOn(users, "passkeyOptions").mockResolvedValue(OPTIONS);
    const register = vi.spyOn(users, "registerPasskey").mockResolvedValue(phone);
    // The card re-reads the list after adding; the second read has the new row.
    vi.spyOn(users, "passkeys").mockResolvedValueOnce([ONE]).mockResolvedValue([ONE, phone]);
    render(<PasskeysCard enabled />);
    await screen.findByText("MacBook");

    await user.click(screen.getByRole("button", { name: /add a passkey/i }));
    await user.type(screen.getByLabelText("Code from your app"), "123456");
    await user.type(screen.getByLabelText("Name"), "Phone");
    await user.click(screen.getByRole("button", { name: /continue/i }));

    await waitFor(() =>
      expect(startRegistration).toHaveBeenCalledWith({ optionsJSON: OPTIONS.options }),
    );
    await waitFor(() =>
      expect(register).toHaveBeenCalledWith({
        nonce: "n1",
        name: "Phone",
        credential: { id: "cred", type: "public-key" },
      }),
    );
    expect(await screen.findByText("Phone")).toBeInTheDocument();
  });

  it("tells the user where the code comes from", async () => {
    const user = userEvent.setup();
    render(<PasskeysCard enabled />);
    await screen.findByText("MacBook");

    await user.click(screen.getByRole("button", { name: /add a passkey/i }));

    // The bare label was not enough for a real user; the field must say
    // which app, and that recovery codes work too.
    expect(screen.getByText(/six-digit code from your authenticator app/i)).toBeInTheDocument();
    expect(screen.getByText(/recovery code/i)).toBeInTheDocument();
  });

  it("a wrong code stays on the form with the message", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "passkeyOptions").mockRejectedValue(
      new ApiError(400, "Bad request", { detail: "That code is not valid." }),
    );
    render(<PasskeysCard enabled />);
    await screen.findByText("MacBook");
    await user.click(screen.getByRole("button", { name: /add a passkey/i }));
    await user.type(screen.getByLabelText("Code from your app"), "000000");
    await user.click(screen.getByRole("button", { name: /continue/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/not valid/i);
    expect(startRegistration).not.toHaveBeenCalled();
  });

  it("a dismissed prompt is a quiet note, not an error", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "passkeyOptions").mockResolvedValue(OPTIONS);
    startRegistration.mockRejectedValue(Object.assign(new Error("x"), { name: "NotAllowedError" }));
    render(<PasskeysCard enabled />);
    await screen.findByText("MacBook");
    await user.click(screen.getByRole("button", { name: /add a passkey/i }));
    await user.type(screen.getByLabelText("Code from your app"), "123456");
    await user.click(screen.getByRole("button", { name: /continue/i }));
    expect(await screen.findByText(/no passkey was added/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("an origin mismatch explains the real cause", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "passkeyOptions").mockResolvedValue(OPTIONS);
    startRegistration.mockRejectedValue(Object.assign(new Error("x"), { name: "SecurityError" }));
    render(<PasskeysCard enabled />);
    await screen.findByText("MacBook");
    await user.click(screen.getByRole("button", { name: /add a passkey/i }));
    await user.type(screen.getByLabelText("Code from your app"), "123456");
    await user.click(screen.getByRole("button", { name: /continue/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/app URL/i);
  });
});

describe("PasskeysCard removing", () => {
  it("asks for a code, then removes and refetches", async () => {
    const user = userEvent.setup();
    const remove = vi.spyOn(users, "removePasskey").mockResolvedValue(undefined);
    render(<PasskeysCard enabled />);
    await screen.findByText("MacBook");

    await user.click(screen.getByRole("button", { name: /remove MacBook/i }));
    await user.type(screen.getByLabelText("Code from your app"), "ABCDE-FGHJK");
    await user.click(screen.getByRole("button", { name: /^remove$/i }));

    await waitFor(() => expect(remove).toHaveBeenCalledWith(1, "ABCDE-FGHJK"));
    await waitFor(() => expect(users.passkeys).toHaveBeenCalledTimes(2));
  });
});
