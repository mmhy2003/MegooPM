import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { customPages, type CustomPage } from "@/lib/api";
import { PagePreviewDialog } from "@/components/custom-pages/page-preview-dialog";

const PAGE: CustomPage = {
  id: 4,
  name: "Maintenance",
  description: "Back soon",
  html: "<h1>Back soon</h1>",
  size_bytes: 18,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
} as CustomPage;

const summary = { id: 4, name: "Maintenance" } as never;

beforeEach(() => {
  vi.spyOn(customPages, "get").mockResolvedValue(PAGE);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderDialog(onEdit = vi.fn()) {
  render(<PagePreviewDialog page={summary} onOpenChange={vi.fn()} onEdit={onEdit} />);
  return onEdit;
}

describe("PagePreviewDialog", () => {
  it("fetches the document the list does not carry", async () => {
    renderDialog();
    // The list holds summaries with a size, not the HTML itself.
    await waitFor(() => expect(customPages.get).toHaveBeenCalledWith(4));
    expect(await screen.findByTitle("Page preview")).toHaveAttribute(
      "srcdoc",
      "<h1>Back soon</h1>",
    );
  });

  it("keeps the preview on an opaque origin", async () => {
    // The line between a preview and a stored-XSS hole in the admin origin:
    // scripts may run, but never with access to this app's DOM or cookies.
    renderDialog();
    const frame = await screen.findByTitle("Page preview");
    expect(frame).toHaveAttribute("sandbox", "allow-scripts");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-same-origin");
  });

  it("names the page it is showing", async () => {
    renderDialog();
    expect(await screen.findByRole("heading", { name: "Maintenance" })).toBeInTheDocument();
  });

  it("shows a failure instead of an empty frame", async () => {
    // A blank iframe reads as "this page is empty", which is a different bug.
    vi.mocked(customPages.get).mockRejectedValue(new Error("boom"));
    renderDialog();
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    expect(screen.queryByTitle("Page preview")).not.toBeInTheDocument();
  });

  it("hands off to the editor", async () => {
    const user = userEvent.setup();
    const onEdit = renderDialog();
    await user.click(await screen.findByRole("button", { name: /edit page/i }));
    expect(onEdit).toHaveBeenCalled();
  });
});
