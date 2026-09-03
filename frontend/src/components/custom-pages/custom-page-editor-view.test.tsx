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
      <textarea aria-label="HTML source" value={value} onChange={(e) => onChange(e.target.value)} />
    );
  },
}));

import { customPages, instanceSettings, type CustomPage, type InstanceSettings } from "@/lib/api";
import { CustomPageEditorView } from "@/components/custom-pages/custom-page-editor-view";

const HTML = "<!doctype html>\n<html><body>hi</body></html>";

const AI_DOC = "<!doctype html>\n<html><body><h1>AI wrote this</h1></body></html>";
const IMG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg";

function makeSettings(llmEnabled: boolean): InstanceSettings {
  return {
    default_site_mode: "not_found",
    crowdsec_ban_mode: "megoopm",
    crowdsec_ban_page_id: null,
    default_site_redirect_url: null,
    default_site_page_id: null,
    llm_enabled: llmEnabled,
    llm_model: llmEnabled ? "gpt-4o" : null,
    llm_api_base: null,
    llm_api_key_set: llmEnabled,
    smtp_enabled: false,
    smtp_host: null,
    smtp_port: 587,
    smtp_security: "starttls",
    smtp_username: null,
    smtp_password_set: false,
    smtp_from: null,
    smtp_from_name: null,
    crowdsec_hub_auto_update: true,
    crowdsec_hub_update_frequency: "daily" as const,
    crowdsec_hub_update_weekday: 6,
    crowdsec_hub_update_hour_utc: 3,
    crowdsec_capi_enabled: false,
    app_url: null,
    updated_at: "2026-09-01T00:00:00Z",
  };
}

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
    vi.spyOn(customPages, "assist").mockResolvedValue({
      html: AI_DOC,
      mode: "tools",
      truncated: false,
      changes: [{ start: 4, end: 4, before: "    <h1>Old</h1>", after: "    <h1>New</h1>" }],
    });
    vi.spyOn(instanceSettings, "get").mockResolvedValue(makeSettings(true));
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("starts a new page from a usable document rather than a blank editor", async () => {
    render(<CustomPageEditorView pageId={null} />);
    const editor = (await screen.findByLabelText("HTML source")) as HTMLTextAreaElement;
    expect(editor.value).toContain("<!doctype html>");
    expect(editor.value).toContain("</html>");
    expect(customPages.get).not.toHaveBeenCalled();
  });

  it("loads an existing page into the form", async () => {
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("Name")).toHaveValue("Access denied"));
    expect(screen.getByLabelText("Description")).toHaveValue("Shown to banned clients");
    expect(screen.getByLabelText("HTML source")).toHaveValue(HTML);
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
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(HTML));
    await user.type(screen.getByLabelText("HTML source"), "!");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(customPages.update).toHaveBeenCalledTimes(1));
    expect(vi.mocked(customPages.update).mock.calls[0][0]).toBe(7);
    expect(push).not.toHaveBeenCalled();
  });

  it("keeps Save disabled until something changes", async () => {
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(HTML));
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await user.type(screen.getByLabelText("HTML source"), "!");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("shows the document's size as it grows", async () => {
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(HTML));
    expect(screen.getByText(`${HTML.length} B`)).toBeInTheDocument();
    await user.type(screen.getByLabelText("HTML source"), "!!");
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

/* -------------------------------------------------------------------------- */
/* AI assistance                                                               */
/* -------------------------------------------------------------------------- */

describe("CustomPageEditorView — AI", () => {
  beforeEach(() => {
    vi.spyOn(customPages, "get").mockResolvedValue(makePage());
    vi.spyOn(customPages, "update").mockResolvedValue(makePage());
    vi.spyOn(customPages, "assist").mockResolvedValue({
      html: AI_DOC,
      mode: "tools",
      truncated: false,
      changes: [{ start: 4, end: 4, before: "    <h1>Old</h1>", after: "    <h1>New</h1>" }],
    });
    vi.spyOn(instanceSettings, "get").mockResolvedValue(makeSettings(true));
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  /** The bar lives behind a toggle so the default layout is unchanged. */
  async function openAi(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole("button", { name: "Ask AI" }));
  }

  async function generate(user: ReturnType<typeof userEvent.setup>, instruction: string) {
    await openAi(user);
    await user.type(await screen.findByLabelText("Instruction"), instruction);
    await user.click(screen.getByRole("button", { name: "Generate" }));
  }

  it("keeps the bar out of the way until it is asked for", async () => {
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(HTML));
    // Default layout is unchanged for anyone not using the feature.
    expect(screen.queryByLabelText("Instruction")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask AI" })).toBeInTheDocument();
  });

  it("sends the instruction and applies the result", async () => {
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(HTML));

    await generate(user, "make it blue");

    await waitFor(() => expect(customPages.assist).toHaveBeenCalledTimes(1));
    expect(vi.mocked(customPages.assist).mock.calls[0][0]).toMatchObject({
      instruction: "make it blue",
      html: HTML,
    });
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(AI_DOC));
  });

  it("elides images before sending and restores them after", async () => {
    const withImage = `<body><img src="${IMG}"></body>`;
    vi.mocked(customPages.get).mockResolvedValue(makePage({ html: withImage }));
    vi.mocked(customPages.assist).mockResolvedValue({
      html: '<body><h1>hi</h1><img src="data:image/png;base64,MEGOOPM_IMAGE_1"></body>',
      mode: "tools",
      truncated: false,
      changes: [],
    });

    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(withImage));

    await generate(user, "add a heading");

    await waitFor(() => expect(customPages.assist).toHaveBeenCalledTimes(1));
    // The base64 never leaves the browser.
    const sent = vi.mocked(customPages.assist).mock.calls[0][0].html;
    expect(sent).not.toContain("iVBORw0KGgo");
    expect(sent).toContain("MEGOOPM_IMAGE_1");
    // ...and it comes back.
    await waitFor(() =>
      expect(screen.getByLabelText("HTML source")).toHaveValue(
        `<body><h1>hi</h1><img src="${IMG}"></body>`,
      ),
    );
  });

  it("reverts to the document from before the AI edit", async () => {
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(HTML));

    await generate(user, "make it blue");
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(AI_DOC));

    await user.click(screen.getByRole("button", { name: "Revert AI edit" }));
    expect(screen.getByLabelText("HTML source")).toHaveValue(HTML);
    expect(screen.queryByRole("button", { name: "Revert AI edit" })).not.toBeInTheDocument();
  });

  it("offers no revert until an AI edit has happened", async () => {
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(HTML));
    expect(screen.queryByRole("button", { name: "Revert AI edit" })).not.toBeInTheDocument();
  });

  it("leaves the document alone when the model call fails", async () => {
    vi.mocked(customPages.assist).mockRejectedValueOnce(new Error("provider said no"));
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(HTML));

    await generate(user, "make it blue");

    expect(await screen.findByRole("alert")).toHaveTextContent("provider said no");
    expect(screen.getByLabelText("HTML source")).toHaveValue(HTML);
  });

  it("refuses to send a document that is too large even elided", async () => {
    vi.mocked(customPages.get).mockResolvedValue(makePage({ html: "x".repeat(200 * 1024 + 1) }));
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await generate(user, "tidy it");

    expect(await screen.findByRole("alert")).toHaveTextContent(/too large/i);
    expect(customPages.assist).not.toHaveBeenCalled();
  });

  it("points at Settings when LLM features are off", async () => {
    vi.mocked(instanceSettings.get).mockResolvedValue(makeSettings(false));
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await openAi(user);

    expect(await screen.findByText(/enable llm features/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("Instruction")).not.toBeInTheDocument();
  });
});

/* -------------------------------------------------------------------------- */
/* What the AI edit changed                                                    */
/* -------------------------------------------------------------------------- */

describe("CustomPageEditorView — change summary", () => {
  beforeEach(() => {
    vi.spyOn(customPages, "get").mockResolvedValue(makePage());
    vi.spyOn(customPages, "update").mockResolvedValue(makePage());
    vi.spyOn(customPages, "assist").mockResolvedValue({
      html: AI_DOC,
      mode: "tools",
      truncated: false,
      changes: [{ start: 4, end: 4, before: "    <h1>Old</h1>", after: "    <h1>New</h1>" }],
    });
    vi.spyOn(instanceSettings, "get").mockResolvedValue(makeSettings(true));
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  async function ask(user: ReturnType<typeof userEvent.setup>, instruction: string) {
    await user.click(await screen.findByRole("button", { name: "Ask AI" }));
    await user.type(await screen.findByLabelText("Instruction"), instruction);
    await user.click(screen.getByRole("button", { name: "Generate" }));
  }

  it("offers no Changes tab before an AI edit", async () => {
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(HTML));

    expect(screen.queryByRole("tab", { name: /changes/i })).not.toBeInTheDocument();
  });

  it("lists the lines the model changed", async () => {
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(HTML));

    await ask(user, "rename the heading");

    expect(await screen.findByText(/1 change/i)).toBeInTheDocument();
    expect(screen.getByText(/line 4/i)).toBeInTheDocument();
    // Indentation preserved: this is the real line the model replaced, and
    // Testing Library collapses whitespace unless told not to.
    const exact = { normalizer: (text: string) => text };
    expect(screen.getByText("    <h1>Old</h1>", exact)).toBeInTheDocument();
    expect(screen.getByText("    <h1>New</h1>", exact)).toBeInTheDocument();
  });

  it("opens the Changes tab so the result is not hidden behind the editor", async () => {
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(HTML));

    await ask(user, "rename the heading");

    // aria-selected, not a base-ui data attribute: this is the contract a
    // screen reader reads, and it does not move when the library renames one.
    expect(
      await screen.findByRole("tab", { name: /changes/i, selected: true }),
    ).toBeInTheDocument();
  });

  it("counts the changes on the tab", async () => {
    vi.mocked(customPages.assist).mockResolvedValue({
      html: AI_DOC,
      mode: "tools",
      truncated: false,
      changes: [
        { start: 4, end: 4, before: "    <h1>Old</h1>", after: "    <h1>New</h1>" },
        { start: 6, end: 6, before: "    <p>a</p>", after: "    <p>b</p>" },
      ],
    });
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(HTML));

    await ask(user, "rename things");

    expect(await screen.findByRole("tab", { name: "Changes 2" })).toBeInTheDocument();
  });

  it("keeps the preview visible while the changes are on screen", async () => {
    // The whole point of moving the diff into the pane: reviewing a change no
    // longer costs you the preview of its effect.
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(HTML));

    await ask(user, "rename the heading");
    await screen.findByText(/1 change/i);

    expect(screen.getByTitle("Page preview")).toBeInTheDocument();
  });

  it("switches back to the editor on demand", async () => {
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(HTML));

    await ask(user, "rename the heading");
    await screen.findByText(/1 change/i);

    await user.click(screen.getByRole("tab", { name: "HTML" }));

    expect(screen.getByLabelText("HTML source")).toHaveValue(AI_DOC);
  });

  it("says the page was rewritten rather than showing an empty change list", async () => {
    // Otherwise a fallback looks like an edit that changed nothing.
    vi.mocked(customPages.assist).mockResolvedValue({
      html: AI_DOC,
      mode: "rewrite",
      truncated: false,
      changes: [],
    });
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(HTML));

    await ask(user, "make it dark");

    expect(await screen.findByText(/rewrote the whole page/i)).toBeInTheDocument();
    expect(screen.queryByText(/1 change/i)).not.toBeInTheDocument();
  });

  it("warns when the model ran out of turns", async () => {
    vi.mocked(customPages.assist).mockResolvedValue({
      html: AI_DOC,
      mode: "tools",
      truncated: true,
      changes: [{ start: 4, end: 4, before: "    <h1>Old</h1>", after: "    <h1>New</h1>" }],
    });
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(HTML));

    await ask(user, "do a lot");

    expect(await screen.findByText(/stopped early/i)).toBeInTheDocument();
  });

  it("clears the change list on revert", async () => {
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(HTML));

    await ask(user, "rename the heading");
    await screen.findByText(/1 change/i);

    await user.click(screen.getByRole("button", { name: "Revert AI edit" }));
    expect(screen.queryByRole("tab", { name: /changes/i })).not.toBeInTheDocument();
  });

  it("dismisses the change list and returns to the editor", async () => {
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(HTML));

    await ask(user, "rename the heading");
    await screen.findByText(/1 change/i);

    await user.click(screen.getByRole("button", { name: "Dismiss changes" }));

    expect(screen.queryByRole("tab", { name: /changes/i })).not.toBeInTheDocument();
    expect(screen.getByLabelText("HTML source")).toHaveValue(AI_DOC);
  });

  it("keeps the edit and the ability to revert it after dismissing", async () => {
    // Dismissing means "I have read this", not "undo this". Losing the undo by
    // reading the summary would make reviewing an edit cost you the escape
    // hatch for it.
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(HTML));

    await ask(user, "rename the heading");
    await screen.findByText(/1 change/i);

    await user.click(screen.getByRole("button", { name: "Dismiss changes" }));

    // The edit itself is untouched...
    expect(screen.getByLabelText("HTML source")).toHaveValue(AI_DOC);
    // ...and the way back is still offered.
    expect(screen.getByRole("button", { name: "Revert AI edit" })).toBeInTheDocument();
  });

  it("shows the summary again after the next edit", async () => {
    // Dismissing hides one result, it does not turn the summary off.
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML source")).toHaveValue(HTML));

    await ask(user, "rename the heading");
    await screen.findByText(/1 change/i);
    await user.click(screen.getByRole("button", { name: "Dismiss changes" }));

    // The prompt bar is already open, so ask() would close it: drive it directly.
    await user.type(await screen.findByLabelText("Instruction"), " again");
    await user.click(screen.getByRole("button", { name: "Generate" }));

    expect(await screen.findByText(/1 change/i)).toBeInTheDocument();
  });
});
