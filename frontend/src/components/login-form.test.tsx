import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  useSearchParams: () => new URLSearchParams(),
}));

const login = vi.fn();
const verifyMfa = vi.fn();
const verifyPasskey = vi.fn();
vi.mock("@/lib/auth/context", () => ({
  useAuth: () => ({ login, verifyMfa, verifyPasskey }),
}));
vi.mock("@/lib/auth/api", () => ({ fetchCapabilities: vi.fn() }));

import { LoginForm } from "@/components/login-form";
import { ApiError } from "@/lib/api/errors";
import { fetchCapabilities } from "@/lib/auth/api";
import { rememberAccount } from "@/lib/auth/recent-accounts";

beforeEach(() => {
  window.localStorage.clear();
  vi.mocked(fetchCapabilities).mockResolvedValue({ password_reset: false, passkeys: false });
  // `null` is "signed in"; a challenge object is "ask for a code".
  login.mockReset().mockResolvedValue(null);
  verifyMfa.mockReset().mockResolvedValue({ recoveryCodesRemaining: null });
  verifyPasskey.mockReset().mockResolvedValue(undefined);
  replace.mockReset();
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

function saveMohamed() {
  rememberAccount({ email: "mm@example.com", full_name: "Mohamed Hammad" });
}

function saveSara() {
  rememberAccount({ email: "sara@example.com", full_name: "Sara Ali" });
}

describe("LoginForm on a browser that has never signed in", () => {
  it("shows no account list at all", async () => {
    render(<LoginForm />);
    await waitFor(() => expect(screen.queryByText(/use another account/i)).not.toBeInTheDocument());
  });

  it("focuses Email, because there is nothing to prefill", async () => {
    render(<LoginForm />);
    await waitFor(() => expect(screen.getByLabelText("Email")).toHaveFocus());
  });
});

describe("LoginForm with a remembered account", () => {
  it("prefills the most recent email", async () => {
    saveSara();
    saveMohamed();
    render(<LoginForm />);

    await waitFor(() => expect(screen.getByLabelText("Email")).toHaveValue("mm@example.com"));
  });

  it("focuses Password, so the returning user types only what is missing", async () => {
    saveMohamed();
    render(<LoginForm />);

    await waitFor(() => expect(screen.getByLabelText("Password")).toHaveFocus());
  });

  it("swaps the email and re-focuses Password when another account is picked", async () => {
    const user = userEvent.setup();
    saveSara();
    saveMohamed();
    render(<LoginForm />);
    await screen.findByRole("button", { name: "Sign in as Sara Ali" });

    await user.click(screen.getByRole("button", { name: "Sign in as Sara Ali" }));

    expect(screen.getByLabelText("Email")).toHaveValue("sara@example.com");
    expect(screen.getByLabelText("Password")).toHaveFocus();
  });

  it("signs in with the account that was picked, not the one prefilled", async () => {
    // The bug this guards: rendering the new email while submitting the old one
    // would sign the operator into the wrong account and look like a backend
    // fault, since the form on screen shows the address they chose.
    const user = userEvent.setup();
    saveSara();
    saveMohamed();
    render(<LoginForm />);
    await screen.findByRole("button", { name: "Sign in as Sara Ali" });

    await user.click(screen.getByRole("button", { name: "Sign in as Sara Ali" }));
    await user.type(screen.getByLabelText("Password"), "hunter2222");
    await user.click(screen.getByRole("button", { name: /continue/i }));

    await waitFor(() => expect(login).toHaveBeenCalledWith("sara@example.com", "hunter2222"));
  });

  it("empties the form and focuses Email for a different account", async () => {
    const user = userEvent.setup();
    saveMohamed();
    render(<LoginForm />);
    await screen.findByRole("button", { name: /use another account/i });

    await user.click(screen.getByRole("button", { name: /use another account/i }));

    expect(screen.getByLabelText("Email")).toHaveValue("");
    expect(screen.getByLabelText("Email")).toHaveFocus();
  });

  it("drops a removed account from the list without reloading", async () => {
    const user = userEvent.setup();
    saveSara();
    saveMohamed();
    render(<LoginForm />);
    await screen.findByRole("button", { name: "Sign in as Sara Ali" });

    await user.click(screen.getByRole("button", { name: "Remove Sara Ali" }));

    expect(screen.queryByRole("button", { name: "Sign in as Sara Ali" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in as Mohamed Hammad" })).toBeInTheDocument();
  });

  it("clears the prefilled email when the selected account is removed", async () => {
    // Otherwise the address stays in the box after the operator asked for it to
    // be forgotten, which is the opposite of what Remove promises.
    const user = userEvent.setup();
    saveMohamed();
    render(<LoginForm />);
    await screen.findByRole("button", { name: "Remove Mohamed Hammad" });

    await user.click(screen.getByRole("button", { name: "Remove Mohamed Hammad" }));

    expect(screen.getByLabelText("Email")).toHaveValue("");
  });

  it("keeps the typed email when a different account is removed", async () => {
    const user = userEvent.setup();
    saveSara();
    saveMohamed();
    render(<LoginForm />);
    await screen.findByRole("button", { name: "Remove Sara Ali" });

    await user.click(screen.getByRole("button", { name: "Remove Sara Ali" }));

    expect(screen.getByLabelText("Email")).toHaveValue("mm@example.com");
  });
});

describe("LoginForm theme control", () => {
  it("offers the theme switcher on a browser that has never signed in", async () => {
    render(<LoginForm />);

    expect(await screen.findByRole("button", { name: "Change theme" })).toBeInTheDocument();
  });

  it("still offers it once the account list appears", async () => {
    // The two layouts differ; a toggle nested inside the single-column branch
    // would vanish the moment an account was remembered.
    saveMohamed();
    render(<LoginForm />);
    await screen.findByRole("button", { name: "Sign in as Mohamed Hammad" });

    expect(screen.getByRole("button", { name: "Change theme" })).toBeInTheDocument();
  });
});

describe("LoginForm forgot-password link", () => {
  it("offers the link when the backend can send email", async () => {
    vi.mocked(fetchCapabilities).mockResolvedValue({ password_reset: true, passkeys: false });
    render(<LoginForm />);

    expect(await screen.findByRole("link", { name: /forgot password/i })).toHaveAttribute(
      "href",
      "/forgot-password",
    );
  });

  it("hides the link when it could not work", async () => {
    // Clicking through to a page that says "check your inbox" when no email
    // will ever arrive is worse than no link.
    vi.mocked(fetchCapabilities).mockResolvedValue({ password_reset: false, passkeys: false });
    render(<LoginForm />);
    await screen.findByLabelText("Email");

    expect(screen.queryByRole("link", { name: /forgot password/i })).not.toBeInTheDocument();
  });

  it("hides the link when capabilities cannot be fetched", async () => {
    vi.mocked(fetchCapabilities).mockRejectedValue(new Error("network"));
    render(<LoginForm />);
    await screen.findByLabelText("Email");

    expect(screen.queryByRole("link", { name: /forgot password/i })).not.toBeInTheDocument();
  });
});

describe("LoginForm second factor", () => {
  async function submitCredentials() {
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "me@example.com");
    await user.type(screen.getByLabelText("Password"), "hunter2222");
    await user.click(screen.getByRole("button", { name: /continue/i }));
    return user;
  }

  it("swaps to a code field when the backend asks for one", async () => {
    login.mockResolvedValue({ mfaToken: "mfa-1", methods: ["totp"] });
    render(<LoginForm />);

    await submitCredentials();

    expect(await screen.findByLabelText("Authentication code")).toHaveFocus();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("sends the code with the challenge token and then signs in", async () => {
    login.mockResolvedValue({ mfaToken: "mfa-1", methods: ["totp"] });
    render(<LoginForm />);
    const user = await submitCredentials();

    await user.type(await screen.findByLabelText("Authentication code"), "123456");
    await user.click(screen.getByRole("button", { name: /verify/i }));

    await waitFor(() => expect(verifyMfa).toHaveBeenCalledWith("mfa-1", "123456"));
    await waitFor(() => expect(replace).toHaveBeenCalled());
  });

  it("offers a recovery-code mode that relabels the field", async () => {
    login.mockResolvedValue({ mfaToken: "mfa-1", methods: ["totp"] });
    render(<LoginForm />);
    const user = await submitCredentials();
    await screen.findByLabelText("Authentication code");

    await user.click(screen.getByRole("button", { name: /use a recovery code/i }));

    expect(screen.getByLabelText("Recovery code")).toBeInTheDocument();
  });

  it("shows the refusal and stays on the code step", async () => {
    login.mockResolvedValue({ mfaToken: "mfa-1", methods: ["totp"] });
    verifyMfa.mockRejectedValue(
      new ApiError(401, "Unauthorized", { detail: "That code is not valid." }),
    );
    render(<LoginForm />);
    const user = await submitCredentials();

    await user.type(await screen.findByLabelText("Authentication code"), "000000");
    await user.click(screen.getByRole("button", { name: /verify/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/not valid/i);
    expect(screen.getByLabelText("Authentication code")).toBeInTheDocument();
  });

  it("goes back to the password step on Back", async () => {
    login.mockResolvedValue({ mfaToken: "mfa-1", methods: ["totp"] });
    render(<LoginForm />);
    const user = await submitCredentials();
    await screen.findByLabelText("Authentication code");

    await user.click(screen.getByRole("button", { name: /back/i }));

    expect(screen.getByLabelText("Password")).toBeInTheDocument();
  });
});

describe("LoginForm passkeys", () => {
  async function reachChallenge(methods: Array<"totp" | "passkey">) {
    login.mockResolvedValue({ mfaToken: "mfa-1", methods });
    render(<LoginForm />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "me@example.com");
    await user.type(screen.getByLabelText("Password"), "hunter2222");
    await user.click(screen.getByRole("button", { name: /continue/i }));
    await screen.findByLabelText("Authentication code");
    return user;
  }

  it("shows the passkey button only when the challenge offers it", async () => {
    await reachChallenge(["totp"]);
    expect(screen.queryByRole("button", { name: /use a passkey/i })).not.toBeInTheDocument();
    cleanup();
    await reachChallenge(["totp", "passkey"]);
    expect(screen.getByRole("button", { name: /use a passkey/i })).toBeInTheDocument();
    // The code field still has focus: reaching for the phone stays fast.
    expect(screen.getByLabelText("Authentication code")).toHaveFocus();
  });

  it("runs the passkey ceremony and signs in", async () => {
    const user = await reachChallenge(["totp", "passkey"]);
    await user.click(screen.getByRole("button", { name: /use a passkey/i }));
    await waitFor(() => expect(verifyPasskey).toHaveBeenCalledWith("mfa-1"));
    await waitFor(() => expect(replace).toHaveBeenCalled());
  });

  it("a dismissed prompt returns quietly to the code field", async () => {
    verifyPasskey.mockRejectedValue(Object.assign(new Error("x"), { name: "NotAllowedError" }));
    const user = await reachChallenge(["totp", "passkey"]);
    await user.click(screen.getByRole("button", { name: /use a passkey/i }));
    await waitFor(() => expect(verifyPasskey).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Authentication code")).toBeInTheDocument();
  });

  it("a refusal shows the backend's message", async () => {
    verifyPasskey.mockRejectedValue(
      new ApiError(401, "Unauthorized", { detail: "That passkey was not accepted." }),
    );
    const user = await reachChallenge(["totp", "passkey"]);
    await user.click(screen.getByRole("button", { name: /use a passkey/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/not accepted/i);
  });

  it("an origin mismatch explains itself", async () => {
    verifyPasskey.mockRejectedValue(Object.assign(new Error("x"), { name: "SecurityError" }));
    const user = await reachChallenge(["totp", "passkey"]);
    await user.click(screen.getByRole("button", { name: /use a passkey/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/app URL/i);
  });
});
