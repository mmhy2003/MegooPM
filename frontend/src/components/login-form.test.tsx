import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  useSearchParams: () => new URLSearchParams(),
}));

const login = vi.fn();
vi.mock("@/lib/auth/context", () => ({ useAuth: () => ({ login }) }));

import { LoginForm } from "@/components/login-form";
import { rememberAccount } from "@/lib/auth/recent-accounts";

beforeEach(() => {
  window.localStorage.clear();
  login.mockReset().mockResolvedValue(undefined);
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
    await waitFor(() =>
      expect(screen.queryByText(/use another account/i)).not.toBeInTheDocument(),
    );
  });

  it("focuses Email, because there is nothing to prefill", async () => {
    render(<LoginForm />);
    await waitFor(() =>
      expect(screen.getByLabelText("Email")).toHaveFocus(),
    );
  });
});

describe("LoginForm with a remembered account", () => {
  it("prefills the most recent email", async () => {
    saveSara();
    saveMohamed();
    render(<LoginForm />);

    await waitFor(() =>
      expect(screen.getByLabelText("Email")).toHaveValue("mm@example.com"),
    );
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

    await waitFor(() =>
      expect(login).toHaveBeenCalledWith("sara@example.com", "hunter2222"),
    );
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

    expect(
      screen.queryByRole("button", { name: "Sign in as Sara Ali" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Sign in as Mohamed Hammad" }),
    ).toBeInTheDocument();
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

    expect(
      await screen.findByRole("button", { name: "Change theme" }),
    ).toBeInTheDocument();
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
