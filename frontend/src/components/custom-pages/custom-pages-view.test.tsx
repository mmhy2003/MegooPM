import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import { customPages, type CustomPageSummary } from "@/lib/api";
import { CustomPagesView } from "@/components/custom-pages/custom-pages-view";

function makeSummary(overrides: Partial<CustomPageSummary> = {}): CustomPageSummary {
  return {
    id: 1,
    name: "Access denied",
    description: "Shown to banned clients",
    size_bytes: 4300,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

describe("CustomPagesView", () => {
  beforeEach(() => {
    push.mockClear();
    vi.spyOn(customPages, "list").mockResolvedValue([makeSummary()]);
    vi.spyOn(customPages, "remove").mockResolvedValue(undefined as never);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("lists each page with a human-readable size", async () => {
    render(<CustomPagesView />);
    expect(await screen.findByText("Access denied")).toBeInTheDocument();
    expect(screen.getByText("Shown to banned clients")).toBeInTheDocument();
    expect(screen.getByText("4.2 KB")).toBeInTheDocument();
  });

  it("says so when there are no pages yet", async () => {
    vi.mocked(customPages.list).mockResolvedValue([]);
    render(<CustomPagesView />);
    expect(await screen.findByText(/No custom pages yet/i)).toBeInTheDocument();
  });

  it("surfaces a load failure with a retry", async () => {
    vi.mocked(customPages.list).mockRejectedValueOnce(new Error("boom"));
    render(<CustomPagesView />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn't load/i);
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("opens the editor for a new page", async () => {
    const user = userEvent.setup();
    render(<CustomPagesView />);
    await user.click(await screen.findByRole("button", { name: /New page/i }));
    expect(push).toHaveBeenCalledWith("/custom-pages/new");
  });

  it("opens the editor for an existing page", async () => {
    const user = userEvent.setup();
    render(<CustomPagesView />);
    await user.click(await screen.findByRole("button", { name: "Edit Access denied" }));
    expect(push).toHaveBeenCalledWith("/custom-pages/1");
  });

  it("deletes a page after the confirmation", async () => {
    const user = userEvent.setup();
    render(<CustomPagesView />);
    await user.click(await screen.findByRole("button", { name: "Delete Access denied" }));
    await user.click(await screen.findByRole("button", { name: "Delete" }));
    await waitFor(() => expect(customPages.remove).toHaveBeenCalledWith(1));
  });
});

describe("CustomPagesView search", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("matches a page by name or description", async () => {
    const user = userEvent.setup();
    vi.spyOn(customPages, "list").mockResolvedValue([
      makeSummary({ id: 1, name: "Maintenance", description: "shown during deploys" }),
      makeSummary({ id: 2, name: "Banned", description: undefined }),
    ]);
    render(<CustomPagesView />);
    await screen.findByRole("searchbox", { name: "Search custom pages" });

    await user.type(screen.getByRole("searchbox"), "deploys");

    expect(screen.getByText("Maintenance")).toBeInTheDocument();
    expect(screen.queryByText("Banned")).not.toBeInTheDocument();
  });

  it("does not choke on a page with no description", async () => {
    // `description` is optional; a naive matcher throws on the row without one.
    const user = userEvent.setup();
    vi.spyOn(customPages, "list").mockResolvedValue([
      makeSummary({ id: 2, name: "Banned", description: undefined }),
    ]);
    render(<CustomPagesView />);
    await screen.findByRole("searchbox", { name: "Search custom pages" });

    await user.type(screen.getByRole("searchbox"), "banned");

    expect(screen.getByText("Banned")).toBeInTheDocument();
  });

  it("distinguishes a filtered-empty table from an empty instance", async () => {
    const user = userEvent.setup();
    vi.spyOn(customPages, "list").mockResolvedValue([]);
    render(<CustomPagesView />);
    await screen.findByRole("searchbox", { name: "Search custom pages" });
    expect(screen.getByText(/no custom pages yet/i)).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox"), "nonesuch");

    expect(screen.getByText(/no custom pages match/i)).toBeInTheDocument();
  });
});
