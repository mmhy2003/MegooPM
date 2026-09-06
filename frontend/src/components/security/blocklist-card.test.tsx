import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { crowdsec, type CapiCredentialHealth, type CrowdSecJobRun } from "@/lib/api";
import { BlocklistCard } from "@/components/security/blocklist-card";

const UNKNOWN: CapiCredentialHealth = { ok: null, detail: null, checked_at: null };
const HEALTHY: CapiCredentialHealth = {
  ok: true,
  detail: null,
  checked_at: "2026-09-06T07:00:00Z",
};
const REJECTED: CapiCredentialHealth = {
  ok: false,
  detail:
    "CrowdSec's central API is refusing these credentials. The engine is running on what it " +
    "loaded at start, but it will not survive a restart until this is fixed. API error: Forbidden",
  checked_at: "2026-09-06T07:00:00Z",
};

const DONE: CrowdSecJobRun = {
  kind: "capi_apply",
  ok: true,
  error: null,
  restarted: true,
  started_at: "2026-09-06T06:00:00Z",
  finished_at: "2026-09-06T06:01:00Z",
  trigger: "manual",
  detail: { enabled: true },
} as CrowdSecJobRun;

function renderCard(over: Partial<React.ComponentProps<typeof BlocklistCard>> = {}) {
  render(
    <BlocklistCard
      desired
      run={DONE}
      running={false}
      reloadConfigured
      credentials={UNKNOWN}
      onChanged={vi.fn()}
      {...over}
    />,
  );
}

beforeEach(() => {
  vi.spyOn(toast, "success").mockImplementation(() => "" as never);
  vi.spyOn(toast, "error").mockImplementation(() => "" as never);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("BlocklistCard credential health", () => {
  it("warns that the engine will not survive a restart", () => {
    // The whole point of the check: this is not cosmetic, and the operator
    // has a limited window to act.
    renderCard({ credentials: REJECTED });

    const warning = screen.getByRole("alert");
    expect(warning).toHaveTextContent(/will not survive a restart/i);
    expect(warning).toHaveTextContent(/Forbidden/);
  });

  it("offers the repair only when the credentials are refused", () => {
    renderCard({ credentials: REJECTED });
    expect(screen.getByRole("button", { name: /re-register/i })).toBeInTheDocument();

    cleanup();
    renderCard({ credentials: HEALTHY });
    expect(screen.queryByRole("button", { name: /re-register/i })).not.toBeInTheDocument();
  });

  it("says nothing when the check has never run", () => {
    // Silence beats a warning nobody can act on: null means "not asked".
    renderCard({ credentials: UNKNOWN });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /re-register/i })).not.toBeInTheDocument();
  });

  it("says nothing while the blocklist is off", () => {
    renderCard({ desired: false, credentials: UNKNOWN });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("re-registers and reloads the card", async () => {
    const user = userEvent.setup();
    const register = vi.spyOn(crowdsec, "capiRegister").mockResolvedValue({ queued: true });
    const onChanged = vi.fn();
    renderCard({ credentials: REJECTED, onChanged });

    await user.click(screen.getByRole("button", { name: /re-register/i }));

    await waitFor(() => expect(register).toHaveBeenCalled());
    expect(onChanged).toHaveBeenCalled();
  });

  it("reports a refused repair instead of pretending it queued", async () => {
    const user = userEvent.setup();
    vi.spyOn(crowdsec, "capiRegister").mockRejectedValue(new Error("reloads are not configured"));
    renderCard({ credentials: REJECTED });

    await user.click(screen.getByRole("button", { name: /re-register/i }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/not configured/i)),
    );
  });

  it("does not offer the repair while a job is running", () => {
    // Registering shares the CAPI lock; the API would answer 409.
    renderCard({ credentials: REJECTED, running: true });
    expect(screen.getByRole("button", { name: /re-register/i })).toBeDisabled();
  });
});
