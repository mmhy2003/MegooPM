"use client";

import { useState, type ClipboardEvent, type KeyboardEvent } from "react";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";
import { addDomains } from "@/components/domains/lib";
import { Badge } from "@/components/ui/badge";

/**
 * A "domain names" field that turns each entry into a removable tag.
 *
 * Enter, comma, space or Tab commits the pending text; a pasted list is split
 * into several tags; Backspace on an empty input removes the last tag; blur
 * commits valid pending text so a forgotten Enter is not lost. Invalid entries
 * stay in the input with an error and are reported through
 * `onPendingInvalidChange` so the surrounding form can refuse to submit.
 */
export function DomainTagsInput({
  id,
  value,
  onChange,
  disabled = false,
  placeholder = "example.com",
  onPendingInvalidChange,
  className,
}: {
  id: string;
  value: string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
  placeholder?: string;
  /** Fires when the pending text becomes invalid (true) or valid/empty (false). */
  onPendingInvalidChange?: (invalid: boolean) => void;
  className?: string;
}) {
  const [pending, setPending] = useState("");
  const [invalid, setInvalid] = useState(false);
  const errorId = `${id}-error`;

  function report(next: boolean) {
    if (next !== invalid) {
      setInvalid(next);
      onPendingInvalidChange?.(next);
    }
  }

  /** Commit `text` into tags; returns whether everything was accepted. */
  function commit(text: string): boolean {
    if (!text.trim()) return true;
    const { next, rejected } = addDomains(value, text);
    if (next.length !== value.length) onChange(next);
    if (rejected.length > 0) {
      setPending(next.length !== value.length ? rejected.join(", ") : text);
      report(true);
      return false;
    }
    setPending("");
    report(false);
    return true;
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === "," || event.key === " ") {
      event.preventDefault();
      commit(pending);
    } else if (event.key === "Tab" && pending.trim()) {
      event.preventDefault();
      commit(pending);
    } else if (event.key === "Backspace" && pending === "" && value.length > 0) {
      event.preventDefault();
      onChange(value.slice(0, -1));
    }
  }

  function onPaste(event: ClipboardEvent<HTMLInputElement>) {
    const text = event.clipboardData.getData("text");
    if (!text) return;
    event.preventDefault();
    commit(`${pending} ${text}`);
  }

  function remove(domain: string) {
    onChange(value.filter((d) => d !== domain));
  }

  return (
    <div className="space-y-1">
      <div
        className={cn(
          "flex min-h-9 w-full flex-wrap items-center gap-1 rounded-md border border-input bg-transparent px-2 py-1 text-sm shadow-xs transition-[color,box-shadow]",
          "focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/50",
          invalid && "border-destructive focus-within:ring-destructive/30",
          disabled && "cursor-not-allowed opacity-50",
          className,
        )}
      >
        {value.map((domain) => (
          <Badge key={domain} variant="secondary" className="gap-1 pr-1 font-mono text-xs">
            {domain}
            <button
              type="button"
              aria-label={`Remove ${domain}`}
              disabled={disabled}
              onClick={() => remove(domain)}
              className="rounded-sm p-0.5 hover:bg-foreground/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <X className="size-3" aria-hidden />
            </button>
          </Badge>
        ))}
        <input
          id={id}
          value={pending}
          onChange={(e) => {
            setPending(e.target.value);
            if (e.target.value === "") report(false);
          }}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          onBlur={() => commit(pending)}
          placeholder={value.length === 0 ? placeholder : ""}
          disabled={disabled}
          autoComplete="off"
          spellCheck={false}
          aria-invalid={invalid ? "true" : undefined}
          aria-describedby={invalid ? errorId : undefined}
          className="min-w-[12ch] flex-1 bg-transparent py-0.5 outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed"
        />
      </div>
      {invalid ? (
        <p id={errorId} className="text-xs text-destructive">
          Not a valid domain name
        </p>
      ) : null}
    </div>
  );
}
