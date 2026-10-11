import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { backups } from "@/lib/api";
import { RestoreDialog } from "@/components/backups/restore-dialog";

const BACKUP = {
  name: "megoopm-20261011-030000-manual.mgb",
  size_bytes: 1024,
  created_at: "2026-10-11T03:00:00Z",
  trigger: "manual",
  key_matches: true,
  app_version: "0.1.0",
  alembic_revision: "0039_backups",
};

function renderDialog() {
  return render(
    <RestoreDialog
      backup={BACKUP as never}
      hostname="pm.example.com"
      open
      onOpenChange={() => {}}
      onStarted={() => {}}
    />,
  );
}

beforeEach(() => {
  vi.spyOn(backups, "restore").mockResolvedValue({ task_id: "t1", status: "PENDING" } as never);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("RestoreDialog", () => {
  it("says what a restore will do before it is possible to start one", () => {
    renderDialog();

    expect(screen.getByText(/replaces this instance/i)).toBeInTheDocument();
    expect(screen.getByText(/everyone is signed out/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^restore$/i })).toBeDisabled();
  });

  it("stays disabled until the hostname is typed exactly", async () => {
    // The typed confirmation is the only thing between a mis-click and a
    // replaced database.
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByLabelText(/type .*pm\.example\.com/i), "pm.example.co");
    expect(screen.getByRole("button", { name: /^restore$/i })).toBeDisabled();

    await user.type(screen.getByLabelText(/type .*pm\.example\.com/i), "m");
    expect(screen.getByRole("button", { name: /^restore$/i })).toBeEnabled();
  });

  it("starts the restore once confirmed", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByLabelText(/type .*pm\.example\.com/i), "pm.example.com");
    await user.click(screen.getByRole("button", { name: /^restore$/i }));

    await waitFor(() => expect(backups.restore).toHaveBeenCalledWith(BACKUP.name));
  });
});
