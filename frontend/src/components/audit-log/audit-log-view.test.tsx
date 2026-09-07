import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { auditLog, type AuditLogEntry } from "@/lib/api";
import { AuditLogView } from "@/components/audit-log/audit-log-view";

// jsdom has no matchMedia, so the real hook would throw; and the layout under
// test must be chosen deliberately, not by whatever width jsdom pretends to be.
const useIsMobile = vi.hoisted(() => vi.fn(() => false));
vi.mock("@/hooks/use-mobile", () => ({ useIsMobile }));

function makeEntry(over: Partial<AuditLogEntry> = {}): AuditLogEntry {
  return {
    id: 1,
    actor: "admin@example.com",
    action: "update",
    object_type: "proxy_host",
    object_id: 7,
    meta: { changes: { is_active: [true, false] } },
    created_at: new Date().toISOString(),
    ...over,
  };
}

function page(items: AuditLogEntry[], total = items.length, offset = 0) {
  return { items, total, limit: 50, offset };
}

beforeEach(() => {
  vi.spyOn(auditLog, "list").mockResolvedValue(page([makeEntry()]));
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useIsMobile.mockReturnValue(false);
});

describe("AuditLogView", () => {
  it("shows who did what to which object", async () => {
    render(<AuditLogView />);

    expect(await screen.findByText("admin@example.com")).toBeInTheDocument();
    expect(screen.getByText("Proxy host #7")).toBeInTheDocument();
    expect(screen.getByText("is_active: true → false")).toBeInTheDocument();
    // Wide viewport: the table, with its header row.
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("names the system when nobody is recorded", async () => {
    // actor is nullable for system-initiated changes; a blank cell reads as a
    // bug rather than as "the scheduler did this".
    vi.mocked(auditLog.list).mockResolvedValue(page([makeEntry({ actor: null })]));
    render(<AuditLogView />);

    expect(await screen.findByText("System")).toBeInTheDocument();
  });

  it("says the log is empty rather than showing an empty table", async () => {
    vi.mocked(auditLog.list).mockResolvedValue(page([], 0));
    render(<AuditLogView />);

    expect(await screen.findByText(/no audit entries/i)).toBeInTheDocument();
  });

  it("opens the full record, raw JSON included", async () => {
    // The summary is lossy by design; this is where nothing is hidden.
    const user = userEvent.setup();
    render(<AuditLogView />);

    await user.click(await screen.findByRole("button", { name: /details/i }));

    expect(await screen.findByText(/"is_active"/)).toBeInTheDocument();
  });

  it("filters by action", async () => {
    const user = userEvent.setup();
    render(<AuditLogView />);
    await screen.findByText("admin@example.com");

    await user.click(screen.getByRole("combobox", { name: "Action" }));
    await user.click(await screen.findByRole("option", { name: "Delete" }));

    await waitFor(() =>
      expect(auditLog.list).toHaveBeenLastCalledWith(expect.objectContaining({ action: "delete" })),
    );
  });

  it("filters by object type", async () => {
    const user = userEvent.setup();
    render(<AuditLogView />);
    await screen.findByText("admin@example.com");

    await user.click(screen.getByRole("combobox", { name: "Object type" }));
    await user.click(await screen.findByRole("option", { name: "Certificate" }));

    await waitFor(() =>
      expect(auditLog.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ objectType: "certificate" }),
      ),
    );
  });

  it("searches by actor", async () => {
    const user = userEvent.setup();
    render(<AuditLogView />);
    await screen.findByText("admin@example.com");

    await user.type(screen.getByRole("searchbox"), "alice");

    await waitFor(() =>
      expect(auditLog.list).toHaveBeenLastCalledWith(expect.objectContaining({ actor: "alice" })),
    );
  });

  it("pages through the log", async () => {
    const user = userEvent.setup();
    vi.mocked(auditLog.list).mockResolvedValue(page([makeEntry()], 120));
    render(<AuditLogView />);
    await screen.findByText("admin@example.com");

    await user.click(screen.getByRole("button", { name: "Next page" }));

    await waitFor(() =>
      expect(auditLog.list).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50 })),
    );
  });

  it("offers no way off the first page when there is only one", async () => {
    render(<AuditLogView />);
    await screen.findByText("admin@example.com");

    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
  });

  it("returns to the first page when a filter changes", async () => {
    // Otherwise page 3 of the old filter shows an empty table for the new one.
    const user = userEvent.setup();
    vi.mocked(auditLog.list).mockResolvedValue(page([makeEntry()], 120));
    render(<AuditLogView />);
    await screen.findByText("admin@example.com");
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() =>
      expect(auditLog.list).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50 })),
    );

    await user.click(screen.getByRole("combobox", { name: "Action" }));
    await user.click(await screen.findByRole("option", { name: "Delete" }));

    await waitFor(() =>
      expect(auditLog.list).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 0 })),
    );
  });

  it("reports a failed load instead of an empty log", async () => {
    // "No audit entries" when the request failed is the worst possible answer.
    vi.mocked(auditLog.list).mockRejectedValue(new Error("nope"));
    render(<AuditLogView />);

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText(/no audit entries/i)).not.toBeInTheDocument();
  });
});

describe("AuditLogView on a phone", () => {
  beforeEach(() => {
    useIsMobile.mockReturnValue(true);
  });

  it("lays each entry out as a card, not a six-column table", async () => {
    // Six columns in 360px crushes the actor into one character per line and
    // pushes Details off the edge; a card keeps every fact readable.
    render(<AuditLogView />);

    expect(await screen.findByText("admin@example.com")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText("Proxy host #7")).toBeInTheDocument();
    expect(screen.getByText("is_active: true → false")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /details/i })).toBeInTheDocument();
  });

  it("still opens the full record", async () => {
    const user = userEvent.setup();
    render(<AuditLogView />);

    await user.click(await screen.findByRole("button", { name: /details/i }));

    expect(await screen.findByText(/"is_active"/)).toBeInTheDocument();
  });

  it("names the system when nobody is recorded", async () => {
    vi.mocked(auditLog.list).mockResolvedValue(page([makeEntry({ actor: null })]));
    render(<AuditLogView />);

    expect(await screen.findByText("System")).toBeInTheDocument();
  });

  it("keeps the empty state and the pager", async () => {
    vi.mocked(auditLog.list).mockResolvedValue(page([], 0));
    render(<AuditLogView />);

    expect(await screen.findByText(/no audit entries/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });
});
