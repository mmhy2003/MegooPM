"use client";

import { useState } from "react";
import Link from "next/link";
import { Loader2, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
  onSubmit: (instruction: string) => void;
  onCancel: () => void;
}) {
  const [instruction, setInstruction] = useState("");

  function submit() {
    const trimmed = instruction.trim();
    if (!trimmed || busy) return;
    onSubmit(trimmed);
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
        <Label htmlFor="ai-instruction">Instruction</Label>
        <Input
          id="ai-instruction"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          onKeyDown={(e) => {
            // One line of instruction; Enter is the obvious way to send it.
            if (e.key === "Enter") {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="make the heading bigger and add a support email"
          disabled={busy}
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
        <Button onClick={submit} className="mb-0.5">
          <Sparkles /> Generate
        </Button>
      )}
    </section>
  );
}
