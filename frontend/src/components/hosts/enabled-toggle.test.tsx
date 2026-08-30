import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { EnabledToggle } from "@/components/hosts/enabled-toggle";

/** A promise plus the handles to settle it, so a request can be held open. */
function deferred() {
  let resolve!: () => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<void>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("EnabledToggle", () => {
  it("names the switch after its row", () => {
    render(<EnabledToggle checked name="old.example.com" onToggle={async () => {}} />);
    // Every row renders one of these, so a bare "Enabled" would leave a screen
    // reader with N identical switches and no way to tell them apart.
    expect(screen.getByLabelText("Enable old.example.com")).toBeInTheDocument();
  });

  it("reflects the current state", () => {
    render(<EnabledToggle checked={false} name="x" onToggle={async () => {}} />);
    expect(screen.getByLabelText("Enable x")).toHaveAttribute("aria-checked", "false");
  });

  it("asks for the opposite of the current state", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn().mockResolvedValue(undefined);
    render(<EnabledToggle checked name="x" onToggle={onToggle} />);

    await user.click(screen.getByLabelText("Enable x"));
    expect(onToggle).toHaveBeenCalledExactlyOnceWith(false);
  });

  it("ignores further clicks while a toggle is in flight", async () => {
    const user = userEvent.setup();
    const d = deferred();
    const onToggle = vi.fn().mockReturnValue(d.promise);
    render(<EnabledToggle checked name="x" onToggle={onToggle} />);

    const toggle = screen.getByLabelText("Enable x");
    await user.click(toggle);
    await user.click(toggle);
    await user.click(toggle);
    // Two conflicting PATCHes would race, and the row would settle on whichever
    // replied last rather than on what the operator last clicked.
    expect(onToggle).toHaveBeenCalledTimes(1);

    d.resolve();
    await waitFor(() => expect(toggle).not.toHaveAttribute("aria-disabled", "true"));
  });

  it("becomes interactive again after a failed toggle", async () => {
    const user = userEvent.setup();
    const d = deferred();
    const onToggle = vi.fn().mockReturnValue(d.promise);
    render(<EnabledToggle checked name="x" onToggle={onToggle} />);
    const toggle = screen.getByLabelText("Enable x");

    await user.click(toggle);
    d.reject(new Error("boom"));

    // A failed request must not strand the switch — the operator has to be able
    // to retry without reloading the page.
    await waitFor(() => expect(toggle).not.toHaveAttribute("aria-disabled", "true"));
    await user.click(toggle);
    expect(onToggle).toHaveBeenCalledTimes(2);
  });

  it("stays inert when the caller disables it", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn().mockResolvedValue(undefined);
    render(<EnabledToggle checked name="x" onToggle={onToggle} disabled />);

    await user.click(screen.getByLabelText("Enable x"));
    expect(onToggle).not.toHaveBeenCalled();
  });
});
