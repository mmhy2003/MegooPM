"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, ImagePlus, Loader2, Sparkles, Undo2, X } from "lucide-react";
import { toast } from "sonner";

import {
  customPages,
  instanceSettings,
  type CustomPage,
  type PageEditChange,
} from "@/lib/api";
import {
  MAX_ASSIST_BYTES,
  MAX_PAGE_BYTES,
  STARTER_HTML,
  describeError,
  describeImageSize,
  elideImages,
  formatBytes,
  htmlByteLength,
  imgTagFor,
  isOverAssistCap,
  isOverPageCap,
  restoreImages,
} from "@/components/custom-pages/lib";
import { HtmlEditor, type HtmlEditorHandle } from "@/components/custom-pages/html-editor";
import { AiPromptBar } from "@/components/custom-pages/ai-prompt-bar";
import { PagePreview } from "@/components/custom-pages/page-preview";
import {
  checkScripts,
  describeScriptProblems,
} from "@/components/custom-pages/script-check";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";

type Form = { name: string; description: string; html: string };

const NEW_FORM: Form = { name: "", description: "", html: STARTER_HTML };

function formFrom(page: CustomPage): Form {
  return { name: page.name, description: page.description, html: page.html };
}

/**
 * Author one custom page: metadata, an HTML editor, and a live preview.
 *
 * `pageId` of `null` is create mode — nothing is fetched and the document
 * starts from {@link STARTER_HTML}. Saving a new page routes to its own editor
 * so a second save updates rather than creating a duplicate.
 */
export function CustomPageEditorView({ pageId }: { pageId: number | null }) {
  const router = useRouter();
  const editor = useRef<HtmlEditorHandle>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const [form, setForm] = useState<Form>(NEW_FORM);
  // The last saved state, so "dirty" is a comparison rather than a flag that
  // can drift out of sync with what the server actually holds.
  const [saved, setSaved] = useState<Form>(NEW_FORM);
  const [loading, setLoading] = useState(pageId !== null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [llmEnabled, setLlmEnabled] = useState(false);
  // The bar is opt-in: the editor is already vertically tight, so the default
  // layout stays exactly as it was for anyone not using AI.
  const [promptOpen, setPromptOpen] = useState(false);
  // The document as it was immediately before the last AI edit. `null` means
  // there is nothing to revert to.
  const [htmlBeforeAi, setHtmlBeforeAi] = useState<string | null>(null);
  // What the last AI edit did, for the operator to read. `null` means no AI
  // edit has happened since the last revert.
  const [lastEdit, setLastEdit] = useState<{
    mode: string;
    truncated: boolean;
    changes: PageEditChange[];
  } | null>(null);
  // Which tab the left pane shows. An AI edit switches to "changes" so the
  // result is not hidden behind the editor; dismissing or reverting sends it
  // back, because the tab it names no longer exists.
  const [pane, setPane] = useState("html");
  const [assisting, setAssisting] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const assistAbort = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    if (pageId === null) return;
    setLoading(true);
    try {
      // A settings failure must not block editing, so it degrades to "off".
      const [page, settings] = await Promise.all([
        customPages.get(pageId),
        instanceSettings.get().catch(() => null),
      ]);
      setForm(formFrom(page));
      setSaved(formFrom(page));
      setLlmEnabled(settings?.llm_enabled ?? false);
      setLoadError(null);
    } catch (err) {
      setLoadError(describeError(err).message);
    } finally {
      setLoading(false);
    }
  }, [pageId]);

  // The IIFE keeps the effect callback itself synchronous; `load` awaits before
  // any setState, so nothing updates state synchronously in the effect body.
  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  // Create mode returns from `load` before fetching anything, but the prompt
  // bar still needs to know whether the feature is on.
  useEffect(() => {
    void (async () => {
      if (pageId !== null) return;
      const settings = await instanceSettings.get().catch(() => null);
      setLlmEnabled(settings?.llm_enabled ?? false);
    })();
  }, [pageId]);

  // Only the interval lives here. The reset to zero belongs with the state
  // transition that causes it — doing it in the effect body is a synchronous
  // setState during an effect, which cascades a render for no reason.
  useEffect(() => {
    if (!assisting) return;
    const started = Date.now();
    const timer = setInterval(
      () => setElapsedSeconds(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => clearInterval(timer);
  }, [assisting]);

  const bytes = htmlByteLength(form.html);
  const overCap = isOverPageCap(form.html);
  const dirty =
    form.name !== saved.name ||
    form.description !== saved.description ||
    form.html !== saved.html;

  function patch(changes: Partial<Form>) {
    setForm((current) => ({ ...current, ...changes }));
  }

  async function handleSave() {
    if (!form.name.trim()) {
      setError("Enter a name for the page.");
      return;
    }
    if (overCap) {
      setError(
        `The document is ${formatBytes(bytes)}; the maximum is ${formatBytes(MAX_PAGE_BYTES)}.`,
      );
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const body = {
        name: form.name.trim(),
        description: form.description,
        html: form.html,
      };
      if (pageId === null) {
        const created = await customPages.create(body);
        toast.success("Page created");
        // Move onto the page's own route so the next save is an update.
        router.push(`/custom-pages/${created.id}`);
      } else {
        const updated = await customPages.update(pageId, body);
        setSaved(formFrom(updated));
        toast.success("Page saved");
      }
    } catch (err) {
      // 409 → the name is taken; 422 → the document exceeds the cap.
      const described = describeError(err);
      setError(described.message);
      toast.error(described.message);
    } finally {
      setSaving(false);
    }
  }

  /** Returns true when the document was actually changed. */
  async function handleAssist(instruction: string): Promise<boolean> {
    // Swap embedded images for placeholders first: one 200 KB screenshot is
    // ~70k tokens of base64 the model cannot read, and it never needs to leave
    // the browser.
    const { html: elided, images } = elideImages(form.html);
    if (isOverAssistCap(elided)) {
      setError(
        `This page is too large for AI editing — ${formatBytes(htmlByteLength(elided))} ` +
          `without its images, against a limit of ${formatBytes(MAX_ASSIST_BYTES)}.`,
      );
      return false;
    }

    setError(null);
    setElapsedSeconds(0);
    setAssisting(true);
    const controller = new AbortController();
    assistAbort.current = controller;
    try {
      const result = await customPages.assist(
        { instruction, html: elided },
        { signal: controller.signal },
      );
      let restored = restoreImages(result.html, images);
      let edit = result;

      // The browser is the only JavaScript parser in this stack that accepts
      // modern syntax on every architecture, so the syntax check happens here
      // rather than in the assist loop. See script-check.ts.
      const problems = checkScripts(restored.html);
      if (problems.length > 0) {
        const repair = await customPages.assist(
          {
            instruction: describeScriptProblems(problems),
            html: elideImages(restored.html).html,
          },
          { signal: controller.signal },
        );
        const repaired = restoreImages(repair.html, images);
        // One round, not a cycle: a model that cannot fix it in one pass will
        // thrash. If it is still broken the operator is told, and still has
        // the preview and the undo.
        if (checkScripts(repaired.html).length === 0) {
          restored = repaired;
          edit = repair;
        } else {
          toast.warning(
            "The page still has a script that will not parse — check it before saving.",
          );
        }
      }

      setHtmlBeforeAi(form.html);
      patch({ html: restored.html });
      setLastEdit({
        mode: edit.mode,
        truncated: edit.truncated ?? false,
        changes: edit.changes ?? [],
      });
      // Show what changed without making the operator go looking for it.
      setPane("changes");
      for (const warning of restored.warnings) toast.warning(warning);
      toast.success("Page updated");
      return true;
    } catch (err) {
      // An aborted request is the operator pressing Cancel, not a failure.
      if (controller.signal.aborted) return false;
      const described = describeError(err);
      setError(described.message);
      toast.error(described.message);
      return false;
    } finally {
      assistAbort.current = null;
      setAssisting(false);
    }
  }

  function handleCancelAssist() {
    assistAbort.current?.abort();
    assistAbort.current = null;
    setAssisting(false);
  }

  function handleRevertAi() {
    if (htmlBeforeAi === null) return;
    patch({ html: htmlBeforeAi });
    setHtmlBeforeAi(null);
    setLastEdit(null);
    // The Changes tab goes with it, so do not leave a dead tab selected.
    setPane("html");
  }

  function handlePickImage(file: File | undefined) {
    if (!file) return;
    const warning = describeImageSize(file.size);
    if (warning) toast.warning(warning);

    const reader = new FileReader();
    reader.onerror = () => toast.error("Couldn't read that file.");
    reader.onload = () => {
      const dataUri = typeof reader.result === "string" ? reader.result : "";
      if (!dataUri) return;
      // Inserted straight into the document: a page carries its own images, so
      // there is nothing to upload and nothing to clean up on delete.
      editor.current?.insertAtCursor(imgTagFor(file.name, dataUri));
    };
    reader.readAsDataURL(file);
  }

  if (loadError) {
    return (
      <div className="mx-auto flex max-w-6xl flex-col items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
        <p className="text-sm text-destructive" role="alert">
          Couldn&apos;t load this page: {loadError}
        </p>
        <Button variant="outline" size="sm" onClick={() => void load()}>
          Retry
        </Button>
      </div>
    );
  }

  return (
    <div className="mx-auto flex h-[calc(100dvh-7rem)] max-w-7xl flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Back to custom pages"
          onClick={() => router.push("/custom-pages")}
        >
          <ArrowLeft />
        </Button>
        <div className="w-56 space-y-1.5">
          <Label htmlFor="cp-name">Name</Label>
          <Input
            id="cp-name"
            value={form.name}
            onChange={(e) => patch({ name: e.target.value })}
            placeholder="Access denied"
            disabled={saving || loading}
          />
        </div>
        <div className="min-w-56 flex-1 space-y-1.5">
          <Label htmlFor="cp-description">Description</Label>
          <Input
            id="cp-description"
            value={form.description}
            onChange={(e) => patch({ description: e.target.value })}
            placeholder="What this page is for"
            disabled={saving || loading}
          />
        </div>
        {htmlBeforeAi !== null ? (
          <Button variant="outline" onClick={handleRevertAi} disabled={saving || assisting}>
            <Undo2 /> Revert AI edit
          </Button>
        ) : null}
        <Button onClick={handleSave} disabled={saving || loading || (pageId !== null && !dirty)}>
          {saving ? <Loader2 className="animate-spin" /> : null}
          {pageId === null ? "Create page" : "Save"}
        </Button>
      </div>

      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}

      {promptOpen ? (
        <AiPromptBar
          enabled={llmEnabled}
          busy={assisting}
          elapsedSeconds={elapsedSeconds}
          onSubmit={handleAssist}
          onCancel={handleCancelAssist}
        />
      ) : null}

      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-2">
        <Tabs
          value={pane}
          onValueChange={(value) => setPane(value as string)}
          render={<section className="flex min-h-0 flex-col gap-0 rounded-xl border" />}
        >
          <div className="flex items-center gap-2 border-b px-3 py-2">
            <TabsList className="h-8 bg-transparent p-0">
              <TabsTab value="html" className="h-7">
                HTML
              </TabsTab>
              {lastEdit ? (
                <TabsTab value="changes" className="h-7">
                  Changes{lastEdit.changes.length ? ` ${lastEdit.changes.length}` : ""}
                </TabsTab>
              ) : null}
            </TabsList>
            <span
              className={`text-xs tabular-nums ${
                overCap ? "text-destructive" : "text-muted-foreground"
              }`}
            >
              {formatBytes(bytes)}
            </span>
            <div className="flex-1" />
            <Button
              variant="outline"
              size="sm"
              disabled={saving || loading}
              onClick={() => setPromptOpen((open) => !open)}
            >
              <Sparkles /> Ask AI
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={saving || loading}
              onClick={() => fileInput.current?.click()}
            >
              <ImagePlus /> Insert image
            </Button>
            <input
              ref={fileInput}
              type="file"
              accept="image/*"
              className="hidden"
              aria-label="Image file"
              onChange={(e) => {
                handlePickImage(e.target.files?.[0]);
                // Reset so picking the same file twice fires again.
                e.target.value = "";
              }}
            />
          </div>
          {/* keepMounted: switching to the diff must not tear down CodeMirror,
              which would throw away the editor's undo history and scroll
              position every time the AI made an edit. */}
          <TabsPanel value="html" keepMounted className="min-h-0 flex-1">
            {loading ? (
              <div className="space-y-2 p-3">
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-4 w-1/2" />
                <Skeleton className="h-4 w-2/3" />
              </div>
            ) : (
              <HtmlEditor
                value={form.html}
                onChange={(html) => patch({ html })}
                readOnly={saving}
                handleRef={editor}
              />
            )}
          </TabsPanel>
          {lastEdit ? (
            /* The diff lives in the pane rather than above it: a long change
               list scrolls here instead of taking its height out of the editor
               and the preview. */
            <TabsPanel value="changes" className="min-h-0 flex-1 overflow-y-auto p-3">
              <div className="flex items-center gap-2">
                {lastEdit.mode === "tools" ? (
                  <p className="text-sm font-medium">
                    {lastEdit.changes.length} change
                    {lastEdit.changes.length === 1 ? "" : "s"} applied
                  </p>
                ) : (
                  <p className="text-sm font-medium">Rewrote the whole page</p>
                )}
                {/* Hides the summary only. The edit stands and `htmlBeforeAi`
                    is untouched, so "Revert AI edit" survives reading this. */}
                <Button
                  variant="ghost"
                  size="sm"
                  className="ms-auto"
                  onClick={() => {
                    setLastEdit(null);
                    setPane("html");
                  }}
                >
                  <X /> Dismiss changes
                </Button>
              </div>
              {lastEdit.truncated ? (
                <p className="mt-2 text-xs text-warning">
                  The model stopped early after reaching its step limit — check
                  the result before saving.
                </p>
              ) : null}
              <div className="mt-2 space-y-2">
                {lastEdit.changes.map((change) => (
                  <div key={`${change.start}-${change.end}`} className="space-y-0.5">
                    <p className="text-xs text-muted-foreground">
                      line {change.start}
                      {change.end !== change.start ? `–${change.end}` : ""}
                    </p>
                    <pre className="overflow-x-auto rounded bg-destructive/10 p-1.5 font-mono text-xs">
                      {change.before}
                    </pre>
                    <pre className="overflow-x-auto rounded bg-success/10 p-1.5 font-mono text-xs">
                      {change.after}
                    </pre>
                  </div>
                ))}
              </div>
            </TabsPanel>
          ) : null}
        </Tabs>

        <section className="flex min-h-0 flex-col rounded-xl border">
          <div className="border-b px-3 py-2 text-sm font-medium">Preview</div>
          <LivePreview html={form.html} />
        </section>
      </div>
    </div>
  );
}

/**
 * The live pane: the shared preview, fed a debounced document.
 *
 * Debounced because this one re-renders on every keystroke; the sandbox that
 * makes rendering arbitrary HTML safe lives in {@link PagePreview}.
 */
function LivePreview({ html }: { html: string }) {
  const [debounced, setDebounced] = useState(html);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(html), 250);
    return () => clearTimeout(timer);
  }, [html]);

  return <PagePreview html={debounced} className="min-h-0 flex-1 rounded-b-xl bg-white" />;
}
