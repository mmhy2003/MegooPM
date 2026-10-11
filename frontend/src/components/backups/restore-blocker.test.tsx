import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { backups } from "@/lib/api";
import { RestoreBlocker } from "@/components/backups/restore-blocker";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("RestoreBlocker", () => {
  it("shows the step while a restore runs", async () => {
    vi.spyOn(backups, "restoreStatus").mockResolvedValue({
      running: true,
      step: "restoring the database",
      started_at: "2026-10-11T03:00:00Z",
      error: null,
    });

    render(<RestoreBlocker />);

    expect(await screen.findByText(/restoring the database/i)).toBeInTheDocument();
  });

  it("tells the admin to sign in again when the restore finishes", async () => {
    // The sessions table came from the backup, so their token is gone.
    const status = vi.spyOn(backups, "restoreStatus");
    status.mockResolvedValueOnce({
      running: true,
      step: "restoring files",
      started_at: "2026-10-11T03:00:00Z",
      error: null,
    });
    status.mockResolvedValue({ running: false, step: null, started_at: null, error: null });

    render(<RestoreBlocker />);

    await waitFor(() => expect(screen.getByText(/sign in again/i)).toBeInTheDocument(), {
      timeout: 5000,
    });
  });

  it("shows why a restore stopped", async () => {
    vi.spyOn(backups, "restoreStatus").mockResolvedValue({
      running: false,
      step: null,
      started_at: null,
      error: "this backup was made with a different SECRET_KEY",
    });

    render(<RestoreBlocker />);

    expect(await screen.findByText(/different SECRET_KEY/)).toBeInTheDocument();
  });
});
