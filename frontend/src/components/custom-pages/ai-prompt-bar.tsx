"use client";

import { useState } from "react";
import Link from "next/link";
import { Loader2, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";

/**
 * The instruction row above the editor split.
 *
 * Presentational: it owns the text and nothing else. The editor owns the
 * request, the abort and the document, so this can be tested without any of
 * them.
 *
 * When LLM features are off the row is still rendered, pointing at Settings.
 * Hiding it would mean nobody discovers the feature exists.
 */
export function AiPromptBar({
  enabled,
  busy,
  elapsedSeconds,
  onSubmit,
  onCancel,
}: {
  enabled: boolean;
  busy: boolean;
  elapsedSeconds: number;
  /** Resolves true when the page was changed, which empties the box. */
  onSubmit: (instruction: string) => void | boolean | Promise<boolean | void>;
  onCancel: () => void;
}) {
  const [instruction, setInstruction] = useState("");

  async function submit() {
    const trimmed = instruction.trim();
    if (!trimmed || busy) return;
    // Cleared only on success: losing a carefully written paragraph to a
    // timeout would mean retyping it to retry.
    if ((await onSubmit(trimmed)) === true) setInstruction("");
  }

  if (!enabled) {
    return (
      <section className="flex items-center gap-2 rounded-xl border border-dashed p-3 text-sm text-muted-foreground">
        <Sparkles className="size-4 shrink-0" />
        <span>
          Enable LLM features in{" "}
          <Link href="/settings" className="underline underline-offset-2">
            Settings
          </Link>{" "}
          to write and edit pages with AI.
        </span>
      </section>
    );
  }

  return (
    <section className="flex items-end gap-2 rounded-xl border p-3">
      <div className="flex-1 space-y-1.5">
        <div className="flex items-baseline justify-between gap-2">
          <Label htmlFor="ai-instruction">Instruction</Label>
          {/* Enter breaks a line here, so the way to send needs saying. */}
          <span className="text-muted-foreground text-xs">Ctrl+Enter to send</span>
        </div>
        <Textarea
          id="ai-instruction"
          rows={3}
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          onKeyDown={(e) => {
            // Enter belongs to the text: an instruction is a paragraph, and
            // asking for two of anything reads better on its own line.
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              void submit();
            }
          }}
          placeholder="make the heading bigger and add a support email"
          disabled={busy}
          className="resize-y"
        />
      </div>
      {busy ? (
        <div className="flex items-center gap-2 pb-0.5">
          <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            {elapsedSeconds}s
          </span>
          <Button variant="outline" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      ) : (
        <Button onClick={() => void submit()} className="mb-0.5">
          <Sparkles /> Generate
        </Button>
      )}
    </section>
  );
}
