import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { instanceSettings } from "@/lib/api";
import { ScheduleCard } from "@/components/backups/schedule-card";

const SETTINGS = {
  backup_frequency: "daily",
  backup_hour_utc: 3,
  backup_weekday: 6,
  backup_keep: 7,
} as never;

beforeEach(() => {
  vi.spyOn(instanceSettings, "updateBackups").mockResolvedValue(SETTINGS);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ScheduleCard", () => {
  it("shows the stored schedule", () => {
    render(<ScheduleCard settings={SETTINGS} onSaved={() => {}} />);

    expect(screen.getByLabelText(/how often/i)).toHaveTextContent(/daily/i);
    expect(screen.getByLabelText(/keep the last/i)).toHaveValue(7);
  });

  it("saves a changed retention count", async () => {
    const user = userEvent.setup();
    render(<ScheduleCard settings={SETTINGS} onSaved={() => {}} />);

    await user.clear(screen.getByLabelText(/keep the last/i));
    await user.type(screen.getByLabelText(/keep the last/i), "14");
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(instanceSettings.updateBackups).toHaveBeenCalledWith(
        expect.objectContaining({ keep: 14, frequency: "daily", hour_utc: 3 }),
      ),
    );
  });

  it("hides the weekday unless the schedule is weekly", async () => {
    const user = userEvent.setup();
    render(<ScheduleCard settings={SETTINGS} onSaved={() => {}} />);
    expect(screen.queryByLabelText(/day of the week/i)).not.toBeInTheDocument();

    await user.click(screen.getByLabelText(/how often/i));
    await user.click(await screen.findByRole("option", { name: /weekly/i }));

    expect(await screen.findByLabelText(/day of the week/i)).toBeInTheDocument();
  });

  it("says plainly that manual backups are never swept", () => {
    // Retention is the one setting here that deletes data.
    render(<ScheduleCard settings={SETTINGS} onSaved={() => {}} />);

    expect(screen.getByText(/manual backups are kept until you delete them/i)).toBeInTheDocument();
  });
});
