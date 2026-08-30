"use client";

import { Switch } from "@/components/ui/switch";

/**
 * One boolean host option: a switch, its label, and a one-line hint.
 *
 * Shared by the proxy / redirection / 404 / stream dialogs so a toggle looks and
 * is announced the same way everywhere. The switch carries an explicit
 * `aria-label`: the wrapping `<label>` also contains the hint, so without it the
 * accessible name is the label and hint run together ("Force SSL Redirect :80 to
 * HTTPS").
 *
 * `className` is for grid placement only (e.g. `sm:col-span-2` for a full-width
 * row); the switch and its text keep a fixed layout.
 */
export function ToggleRow({
  label,
  hint,
  checked,
  onCheckedChange,
  disabled,
  className,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onCheckedChange: (value: boolean) => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <label className={`flex items-start gap-2 ${className ?? ""}`}>
      <Switch
        aria-label={label}
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
      />
      <span className="space-y-0.5">
        <span className="block text-sm font-medium leading-none">{label}</span>
        <span className="block text-xs text-muted-foreground">{hint}</span>
      </span>
    </label>
  );
}
