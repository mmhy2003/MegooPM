"use client";

import { useEffect, useImperativeHandle, useRef } from "react";
import { EditorState, StateEffect, StateField, type Extension } from "@codemirror/state";
import { EditorView, Decoration, ViewPlugin, WidgetType } from "@codemirror/view";
import type { DecorationSet, ViewUpdate } from "@codemirror/view";
import { basicSetup } from "codemirror";
import { html as htmlLang } from "@codemirror/lang-html";
import { oneDark } from "@codemirror/theme-one-dark";
import { useTheme } from "next-themes";

import { DATA_URI_PATTERN, dataUriSummary } from "@/components/custom-pages/lib";

/** Imperative handle so the toolbar can insert text at the cursor. */
export type HtmlEditorHandle = {
  insertAtCursor: (text: string) => void;
};

/* -------------------------------------------------------------------------- */
/* Data-URI folding                                                            */
/* -------------------------------------------------------------------------- */

/**
 * One embedded image is tens of thousands of base64 characters. Left alone it
 * buries the actual markup, so each data URI is replaced by a short summary
 * chip. Clicking the chip removes the decoration for that URI and reveals the
 * real text, which is the escape hatch for anyone who needs to edit it.
 */
class DataUriWidget extends WidgetType {
  constructor(
    readonly label: string,
    readonly from: number,
  ) {
    super();
  }

  eq(other: DataUriWidget) {
    return other.label === this.label && other.from === this.from;
  }

  toDOM(view: EditorView) {
    const chip = document.createElement("span");
    chip.className = "cm-data-uri-chip";
    chip.textContent = this.label;
    chip.title = "Click to reveal the full data URI";
    chip.onclick = () => {
      view.dispatch({ effects: revealDataUri.of(this.from) });
    };
    return chip;
  }

  ignoreEvent() {
    return false;
  }
}

/** Positions the user has explicitly expanded; those stay unfolded. */
const revealDataUri = StateEffect.define<number>();

const revealedUris = StateField.define<Set<number>>({
  create: () => new Set(),
  update(value, tr) {
    let next = value;
    for (const effect of tr.effects) {
      if (effect.is(revealDataUri)) {
        next = new Set(next);
        next.add(effect.value);
      }
    }
    // Positions shift as the document changes; drop them rather than track
    // them, so a stale entry can never unfold the wrong span.
    return tr.docChanged && next.size > 0 ? new Set() : next;
  },
});

function buildDecorations(view: EditorView): DecorationSet {
  const revealed = view.state.field(revealedUris);
  const pattern = new RegExp(DATA_URI_PATTERN.source, "g");
  const ranges: ReturnType<typeof Decoration.replace>[] = [];
  const positions: { from: number; to: number }[] = [];

  for (const { from, to } of view.visibleRanges) {
    const text = view.state.doc.sliceString(from, to);
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(text)) !== null) {
      const start = from + match.index;
      const end = start + match[0].length;
      if (revealed.has(start)) continue;
      ranges.push(
        Decoration.replace({ widget: new DataUriWidget(dataUriSummary(match[0]), start) }),
      );
      positions.push({ from: start, to: end });
    }
  }

  return Decoration.set(
    ranges.map((deco, i) => deco.range(positions[i].from, positions[i].to)),
    true,
  );
}

const foldDataUris = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet;

    constructor(view: EditorView) {
      this.decorations = buildDecorations(view);
    }

    update(update: ViewUpdate) {
      if (update.docChanged || update.viewportChanged || update.state.field(revealedUris).size) {
        this.decorations = buildDecorations(update.view);
      }
    }
  },
  { decorations: (plugin) => plugin.decorations },
);

const chipTheme = EditorView.baseTheme({
  ".cm-data-uri-chip": {
    padding: "0 6px",
    borderRadius: "4px",
    border: "1px solid rgba(127,127,127,0.4)",
    background: "rgba(127,127,127,0.12)",
    fontStyle: "italic",
    cursor: "pointer",
  },
});

/* -------------------------------------------------------------------------- */
/* Editor                                                                      */
/* -------------------------------------------------------------------------- */

/**
 * CodeMirror 6 bound to a controlled `value`. Only the two editor routes import
 * it, so Next's route-level code splitting keeps its weight out of every other
 * page's bundle.
 */
export function HtmlEditor({
  value,
  onChange,
  readOnly = false,
  handleRef,
}: {
  value: string;
  onChange: (value: string) => void;
  readOnly?: boolean;
  handleRef?: React.Ref<HtmlEditorHandle>;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  // Kept in a ref so changing the callback never tears down the editor. Synced
  // in an effect rather than during render, which React forbids for refs.
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  const { resolvedTheme } = useTheme();
  const dark = resolvedTheme === "dark";

  useImperativeHandle(handleRef, () => ({
    insertAtCursor(text: string) {
      const current = view.current;
      if (!current) return;
      const { from, to } = current.state.selection.main;
      current.dispatch({
        changes: { from, to, insert: text },
        selection: { anchor: from + text.length },
      });
      current.focus();
    },
  }));

  useEffect(() => {
    if (!host.current) return;

    const extensions: Extension[] = [
      basicSetup,
      htmlLang(),
      revealedUris,
      foldDataUris,
      chipTheme,
      EditorView.lineWrapping,
      EditorState.readOnly.of(readOnly),
      EditorView.updateListener.of((update) => {
        if (update.docChanged) onChangeRef.current(update.state.doc.toString());
      }),
    ];
    if (dark) extensions.push(oneDark);

    const instance = new EditorView({
      state: EditorState.create({ doc: value, extensions }),
      parent: host.current,
    });
    view.current = instance;
    return () => {
      instance.destroy();
      view.current = null;
    };
    // `value` is intentionally omitted: it seeds the document once, and later
    // edits flow out through onChange. Re-running on every keystroke would
    // rebuild the editor and lose the cursor.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dark, readOnly]);

  // Adopt an externally replaced document (e.g. a reset) without remounting.
  useEffect(() => {
    const current = view.current;
    if (!current) return;
    const existing = current.state.doc.toString();
    if (existing === value) return;
    current.dispatch({ changes: { from: 0, to: existing.length, insert: value } });
  }, [value]);

  return <div ref={host} className="h-full overflow-auto text-sm" data-testid="html-editor" />;
}

export default HtmlEditor;
