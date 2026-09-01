import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

// CodeMirror needs layout APIs jsdom does not provide, and the editor is loaded
// dynamically in the real app anyway. A textarea stands in so the view's own
// behaviour — loading, dirty state, saving, image insertion — stays testable.
vi.mock("@/components/custom-pages/html-editor", () => ({
  HtmlEditor: ({
    value,
    onChange,
    handleRef,
  }: {
    value: string;
    onChange: (v: string) => void;
    handleRef?: { current: unknown };
  }) => {
    if (handleRef) {
      handleRef.current = {
        insertAtCursor: (text: string) => onChange(value + text),
      };
    }
    return (
      <textarea aria-label="HTML" value={value} onChange={(e) => onChange(e.target.value)} />
    );
  },
}));

import { customPages, type CustomPage } from "@/lib/api";
import { CustomPageEditorView } from "@/components/custom-pages/custom-page-editor-view";

const HTML = "<!doctype html>\n<html><body>hi</body></html>";

function makePage(overrides: Partial<CustomPage> = {}): CustomPage {
  return {
    id: 7,
    name: "Access denied",
    description: "Shown to banned clients",
    html: HTML,
    size_bytes: HTML.length,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

describe("CustomPageEditorView", () => {
  beforeEach(() => {
    push.mockClear();
    vi.spyOn(customPages, "get").mockResolvedValue(makePage());
    vi.spyOn(customPages, "create").mockResolvedValue(makePage());
    vi.spyOn(customPages, "update").mockResolvedValue(makePage());
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("starts a new page from a usable document rather than a blank editor", async () => {
    render(<CustomPageEditorView pageId={null} />);
    const editor = (await screen.findByLabelText("HTML")) as HTMLTextAreaElement;
    expect(editor.value).toContain("<!doctype html>");
    expect(editor.value).toContain("</html>");
    expect(customPages.get).not.toHaveBeenCalled();
  });

  it("loads an existing page into the form", async () => {
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("Name")).toHaveValue("Access denied"));
    expect(screen.getByLabelText("Description")).toHaveValue("Shown to banned clients");
    expect(screen.getByLabelText("HTML")).toHaveValue(HTML);
  });

  it("creates the page and routes to its editor", async () => {
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={null} />);
    await user.type(await screen.findByLabelText("Name"), "Maintenance");
    await user.click(screen.getByRole("button", { name: "Create page" }));

    await waitFor(() => expect(customPages.create).toHaveBeenCalledTimes(1));
    expect(vi.mocked(customPages.create).mock.calls[0][0]).toMatchObject({
      name: "Maintenance",
    });
    expect(push).toHaveBeenCalledWith("/custom-pages/7");
  });

  it("refuses to save without a name", async () => {
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={null} />);
    await user.click(await screen.findByRole("button", { name: "Create page" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/name/i);
    expect(customPages.create).not.toHaveBeenCalled();
  });

  it("saves an edit and stays put", async () => {
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML")).toHaveValue(HTML));
    await user.type(screen.getByLabelText("HTML"), "!");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(customPages.update).toHaveBeenCalledTimes(1));
    expect(vi.mocked(customPages.update).mock.calls[0][0]).toBe(7);
    expect(push).not.toHaveBeenCalled();
  });

  it("keeps Save disabled until something changes", async () => {
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML")).toHaveValue(HTML));
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await user.type(screen.getByLabelText("HTML"), "!");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("shows the document's size as it grows", async () => {
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML")).toHaveValue(HTML));
    expect(screen.getByText(`${HTML.length} B`)).toBeInTheDocument();
    await user.type(screen.getByLabelText("HTML"), "!!");
    expect(screen.getByText(`${HTML.length + 2} B`)).toBeInTheDocument();
  });

  it("previews the document in a sandboxed iframe that cannot reach the app", async () => {
    render(<CustomPageEditorView pageId={7} />);
    const frame = await screen.findByTitle("Page preview");
    // allow-scripts WITHOUT allow-same-origin: scripts run on an opaque origin,
    // so page content can never touch the admin app.
    expect(frame).toHaveAttribute("sandbox", "allow-scripts");
  });

  it("surfaces a load failure with a retry", async () => {
    vi.mocked(customPages.get).mockRejectedValueOnce(new Error("boom"));
    render(<CustomPageEditorView pageId={7} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn't load/i);
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
