import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { backups, instanceSettings } from "@/lib/api";
import { BackupsView } from "@/components/backups/backups-view";

vi.mock("@/lib/events", () => ({ subscribeToEvents: () => () => {} }));

function makeBackup(over: Partial<Awaited<ReturnType<typeof backups.list>>["items"][number]> = {}) {
  return {
    name: "megoopm-20261011-030000-manual.mgb",
    size_bytes: 5 * 1024 * 1024,
    created_at: "2026-10-11T03:00:00Z",
    trigger: "manual",
    key_matches: true,
    app_version: "0.1.0",
    alembic_revision: "0039_backups",
    ...over,
  };
}

beforeEach(() => {
  vi.spyOn(backups, "list").mockResolvedValue({ items: [makeBackup()], free_bytes: 10 ** 10 });
  vi.spyOn(backups, "create").mockResolvedValue({ task_id: "t1", status: "PENDING" } as never);
  vi.spyOn(backups, "remove").mockResolvedValue(undefined as never);
  vi.spyOn(backups, "restoreStatus").mockResolvedValue({
    running: false,
    step: null,
    started_at: null,
    error: null,
  });
  vi.spyOn(instanceSettings, "get").mockResolvedValue({
    backup_frequency: "off",
    backup_hour_utc: 3,
    backup_weekday: 6,
    backup_keep: 7,
    last_backup_ok: true,
    last_backup_at: "2026-10-11T03:00:05Z",
    last_backup_message: null,
  } as never);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("BackupsView", () => {
  it("lists what is on the server", async () => {
    render(<BackupsView />);

    expect(await screen.findByText("megoopm-20261011-030000-manual.mgb")).toBeInTheDocument();
    expect(screen.getByText("5.0 MB")).toBeInTheDocument();
    // Exact: the file name contains "manual" too, so a loose matcher
    // would pass without the column ever rendering.
    expect(screen.getByText("Manual")).toBeInTheDocument();
  });

  it("takes a backup on request", async () => {
    const user = userEvent.setup();
    render(<BackupsView />);

    await user.click(await screen.findByRole("button", { name: /back up now/i }));

    expect(backups.create).toHaveBeenCalledTimes(1);
  });

  it("warns about a backup this instance cannot read", async () => {
    // Restoring it would fail, and its secrets would be unreadable: say so
    // before an admin plans a recovery around it.
    vi.mocked(backups.list).mockResolvedValue({
      items: [makeBackup({ key_matches: false, app_version: null })],
      free_bytes: 10 ** 10,
    });
    render(<BackupsView />);

    expect(await screen.findByText(/different secret key/i)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /restore/i })).toBeDisabled();
  });

  it("says when there is nothing to restore from", async () => {
    vi.mocked(backups.list).mockResolvedValue({ items: [], free_bytes: 10 ** 10 });
    render(<BackupsView />);

    expect(await screen.findByText(/no backups yet/i)).toBeInTheDocument();
  });

  it("deletes a backup after confirmation", async () => {
    const user = userEvent.setup();
    render(<BackupsView />);

    await user.click(await screen.findByRole("button", { name: /delete/i }));
    await user.click(await screen.findByRole("button", { name: /^delete$/i }));

    await waitFor(() =>
      expect(backups.remove).toHaveBeenCalledWith("megoopm-20261011-030000-manual.mgb"),
    );
  });
});
