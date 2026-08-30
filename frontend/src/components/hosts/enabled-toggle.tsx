"use client";

import { useState } from "react";

import { Switch } from "@/components/ui/switch";

/**
 * The enable/disable switch shown on each row of a host list.
 *
 * It owns exactly one piece of state: whether a toggle is currently in flight.
 * The row's `checked` value stays with the view that holds the list, so there is
 * one source of truth for row data and the optimistic update (and its rollback)
 * happens where the data lives.
 *
 * The in-flight guard matters because these lists are one click from a config
 * write: two rapid clicks would put two conflicting PATCHes in the air and the
 * row would settle on whichever replied last rather than on what was last
 * clicked.
 *
 * `name` identifies the row in the accessible name. Every row renders one of
 * these, so a bare "Enabled" would leave a screen reader reading out a column of
 * identical switches with no way to tell which host each belongs to.
 */
export function EnabledToggle({
  checked,
  name,
  onToggle,
  disabled,
}: {
  checked: boolean;
  name: string;
  onToggle: (next: boolean) => Promise<void>;
  disabled?: boolean;
}) {
  const [pending, setPending] = useState(false);

  async function handleChange(next: boolean) {
    if (pending || disabled) return;
    setPending(true);
    try {
      await onToggle(next);
    } catch {
      // `onToggle` owns reporting and rollback — the caller holds the row data,
      // so it is the only thing that can put the row back. Swallowing here is
      // what keeps a rejection from escaping as an unhandled promise rejection:
      // nothing awaits the promise this handler returns.
    } finally {
      // Always clears, including on rejection — a failed request must not
      // strand the switch, or the operator cannot retry without a reload.
      setPending(false);
    }
  }

  return (
    <Switch
      aria-label={`Enable ${name}`}
      checked={checked}
      onCheckedChange={handleChange}
      disabled={disabled || pending}
    />
  );
}
