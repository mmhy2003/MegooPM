import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AccountList } from "@/components/login/account-list";
import type { RecentAccount } from "@/lib/auth/recent-accounts";

const MOHAMED: RecentAccount = {
  email: "mm@example.com",
  full_name: "Mohamed Hammad",
  lastUsedAt: "2026-09-03T00:00:00Z",
};
const SARA: RecentAccount = {
  email: "sara@example.com",
  full_name: "Sara Ali",
  lastUsedAt: "2026-09-02T00:00:00Z",
};

function renderList(over: Partial<React.ComponentProps<typeof AccountList>> = {}) {
  const props = {
    accounts: [MOHAMED, SARA],
    selectedEmail: MOHAMED.email,
    onSelect: vi.fn(),
    onForget: vi.fn(),
    onUseAnother: vi.fn(),
    ...over,
  };
  render(<AccountList {...props} />);
  return props;
}

afterEach(() => cleanup());

describe("AccountList", () => {
  it("renders nothing at all when no account has signed in here", () => {
    // The login page must look untouched on a fresh browser.
    const { container } = render(
      <AccountList
        accounts={[]}
        selectedEmail={null}
        onSelect={vi.fn()}
        onForget={vi.fn()}
        onUseAnother={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("names each account by the same rule as the topbar avatar", () => {
    renderList();
    expect(screen.getByRole("button", { name: "Sign in as Mohamed Hammad" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in as Sara Ali" })).toBeInTheDocument();
  });

  it("shows the email, since two people can share a name", () => {
    renderList();
    expect(screen.getByText("mm@example.com")).toBeInTheDocument();
  });

  it("marks which account is selected for assistive technology", () => {
    renderList();
    expect(screen.getByRole("button", { name: "Sign in as Mohamed Hammad" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Sign in as Sara Ali" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("reports which account was chosen", async () => {
    const user = userEvent.setup();
    const { onSelect } = renderList();

    await user.click(screen.getByRole("button", { name: "Sign in as Sara Ali" }));

    expect(onSelect).toHaveBeenCalledWith(SARA);
  });

  it("offers a per-account way out, named after the account", async () => {
    // The shared-machine escape hatch. Without it the feature is one-way.
    const user = userEvent.setup();
    const { onForget } = renderList();

    await user.click(screen.getByRole("button", { name: "Remove Sara Ali" }));

    expect(onForget).toHaveBeenCalledWith(SARA.email);
  });

  it("does not select an account when its Remove button is clicked", async () => {
    // Remove sits inside the account button's row; a bubbled click would both
    // delete the account and sign the user in as it.
    const user = userEvent.setup();
    const { onSelect, onForget } = renderList();

    await user.click(screen.getByRole("button", { name: "Remove Sara Ali" }));

    expect(onForget).toHaveBeenCalled();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("offers an escape to a fresh, empty form", async () => {
    const user = userEvent.setup();
    const { onUseAnother } = renderList();

    await user.click(screen.getByRole("button", { name: /use another account/i }));

    expect(onUseAnother).toHaveBeenCalled();
  });

  it("falls back to the email when an account has no name", () => {
    renderList({
      accounts: [{ email: "nameless@example.com", full_name: "", lastUsedAt: "" }],
      selectedEmail: null,
    });
    expect(
      screen.getByRole("button", { name: "Sign in as nameless@example.com" }),
    ).toBeInTheDocument();
  });
});
