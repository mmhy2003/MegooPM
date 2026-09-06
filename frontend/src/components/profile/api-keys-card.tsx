"use client";

import { useCallback, useEffect, useState } from "react";
import { Copy, Terminal, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { users, type ApiKey } from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

/** The order is the recommendation: an expiry first, Never last. */
const EXPIRY_OPTIONS = [
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "365", label: "1 year" },
  { value: "custom", label: "Custom date" },
  { value: "never", label: "Never" },
] as const;

type Mode =
  | { kind: "idle" }
  | { kind: "create" }
  | { kind: "created"; token: string }
  | { kind: "delete"; key: ApiKey };

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function hasExpired(key: ApiKey): boolean {
  return key.expires_at !== null && new Date(key.expires_at).getTime() <= Date.now();
}

/** ISO for the API, or null for a key that never expires.
 *
 * "custom" with no date never reaches here — Create is disabled — because
 * quietly reading it as "never" would hand back a key that outlives everything
 * to someone who explicitly asked for an expiry.
 */
function expiresAt(choice: string, customDate: string): string | null {
  if (choice === "never" || choice === "custom") {
    return customDate && choice === "custom" ? new Date(customDate).toISOString() : null;
  }
  const at = new Date();
  at.setDate(at.getDate() + Number(choice));
  return at.toISOString();
}

/**
 * The keys this account can use against the API, and the three flows that
 * change them.
 *
 * A key acts as its owner — a member's key is read-only because their role is —
 * except that it cannot manage accounts or users. That boundary is enforced by
 * the API; this card only explains it.
 */
export function ApiKeysCard() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [mode, setMode] = useState<Mode>({ kind: "idle" });
  const [name, setName] = useState("");
  const [choice, setChoice] = useState<string>("30");
  const [customDate, setCustomDate] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setKeys(await users.apiKeys());
    } catch (err) {
      setError(describeError(err).message);
    }
  }, []);

  // The IIFE keeps the effect callback synchronous; `load` awaits before any
  // setState, so nothing updates state synchronously in the effect body.
  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  function reset() {
    setMode({ kind: "idle" });
    setName("");
    setChoice("30");
    setCustomDate("");
    setAcknowledged(false);
    setBusy(false);
    setError(null);
  }

  async function create() {
    setError(null);
    setBusy(true);
    try {
      const created = await users.createApiKey({
        name: name.trim(),
        expires_at: expiresAt(choice, customDate),
      });
      setName("");
      setBusy(false);
      // Straight into the reveal: this is the only time the token exists.
      setMode({ kind: "created", token: created.token });
      await load();
    } catch (err) {
      setError(describeError(err).message);
      setBusy(false);
    }
  }

  async function setEnabled(key: ApiKey, enabled: boolean) {
    // Flip the row first, put it back if the write fails: a row that claims a
    // key is off while it still works is the one state to never be wrong about.
    setKeys((prev) => prev.map((k) => (k.id === key.id ? { ...k, enabled } : k)));
    try {
      await users.setApiKeyEnabled(key.id, enabled);
    } catch (err) {
      setKeys((prev) => prev.map((k) => (k.id === key.id ? { ...k, enabled: !enabled } : k)));
      toast.error(describeError(err).message);
    }
  }

  async function remove(key: ApiKey) {
    setBusy(true);
    try {
      await users.deleteApiKey(key.id);
      toast.success(`${key.name} revoked`);
      reset();
      await load();
    } catch (err) {
      setError(describeError(err).message);
      setBusy(false);
    }
  }

  async function copyToken(token: string) {
    try {
      await navigator.clipboard.writeText(token);
      toast.success("API key copied");
    } catch {
      toast.error("Could not copy. Select the key and copy it by hand.");
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Terminal className="size-4" /> API keys
        </CardTitle>
        <CardDescription>
          Let a script use the API as you, with your permissions. A key cannot manage accounts or
          users — that needs a browser session — so a key that leaks cannot lock you out.
        </CardDescription>
      </CardHeader>

      <CardContent className="grid gap-4">
        {keys.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            No API keys yet. A key is a password for a script: it is shown once, works until you
            revoke it, and does exactly what your account can do.
          </p>
        ) : (
          <ul className="divide-y rounded-lg border">
            {keys.map((key) => (
              <li
                key={key.id}
                className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
              >
                <div className="min-w-0">
                  <div className="font-medium">{key.name}</div>
                  <div className="text-muted-foreground truncate font-mono text-xs">
                    {key.token_prefix}…
                  </div>
                  <div className="text-muted-foreground text-xs">
                    {/* Each fact in its own span: split across text nodes, a
                        reader (and a test) sees "Never" and "used" apart. */}
                    <span>Created {formatDate(key.created_at)}</span>
                    {" · "}
                    <span>
                      {key.last_used_at
                        ? `Last used ${formatDate(key.last_used_at)}`
                        : "Never used"}
                    </span>
                    {" · "}
                    {key.expires_at === null ? (
                      <span>No expiry</span>
                    ) : hasExpired(key) ? (
                      <span className="text-destructive">Expired</span>
                    ) : (
                      <span>Expires {formatDate(key.expires_at)}</span>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Switch
                    aria-label={`Enable ${key.name}`}
                    checked={key.enabled}
                    onCheckedChange={(next) => void setEnabled(key, next)}
                    disabled={mode.kind !== "idle"}
                  />
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Delete ${key.name}`}
                    onClick={() => setMode({ kind: "delete", key })}
                    disabled={mode.kind !== "idle"}
                  >
                    <Trash2 />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}

        {mode.kind === "create" ? (
          <div className="grid gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="api-key-name">Name</Label>
              <Input
                id="api-key-name"
                placeholder="CI deploy"
                maxLength={64}
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={busy}
                autoFocus
              />
              <p className="text-muted-foreground text-xs">
                What uses it. A key you cannot name is a key nobody dares revoke.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="api-key-expiry">Expires</Label>
              <Select
                value={choice}
                onValueChange={(value) => setChoice(value as string)}
                items={Object.fromEntries(EXPIRY_OPTIONS.map((o) => [o.value, o.label]))}
              >
                <SelectTrigger id="api-key-expiry" disabled={busy} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {EXPIRY_OPTIONS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {choice === "custom" ? (
              <div className="space-y-1.5">
                <Label htmlFor="api-key-date">Expiry date</Label>
                <Input
                  id="api-key-date"
                  type="date"
                  value={customDate}
                  onChange={(e) => setCustomDate(e.target.value)}
                  disabled={busy}
                />
              </div>
            ) : null}
          </div>
        ) : null}

        {mode.kind === "created" ? (
          <div className="space-y-3">
            <p className="text-sm font-medium">
              Copy your key. This is the <strong>only time</strong> it is shown.
            </p>
            <p className="text-muted-foreground text-sm">
              Only a hash of it is stored, so it cannot be shown again or recovered. If you lose it,
              delete this key and create another.
            </p>
            <p className="bg-muted rounded-lg border p-3 font-mono text-sm break-all">
              {mode.token}
            </p>
            <Button variant="outline" size="sm" onClick={() => void copyToken(mode.token)}>
              <Copy /> Copy key
            </Button>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(e) => setAcknowledged(e.target.checked)}
                aria-label="I have saved this key"
              />
              I have saved this key somewhere safe.
            </label>
          </div>
        ) : null}

        {mode.kind === "delete" ? (
          <p className="text-sm">
            Revoke <strong>{mode.key.name}</strong>? Anything using it stops on its next request.
            This cannot be undone.
          </p>
        ) : null}

        {error ? (
          <p role="alert" className="text-destructive text-sm">
            {error}
          </p>
        ) : null}

        {mode.kind === "idle" && keys.length > 0 ? (
          // A literal placeholder, not this account's prefix: an example is for
          // copying the shape, and pasting a real prefix into a shell teaches
          // the wrong habit.
          <code className="text-muted-foreground block font-mono text-xs break-all">
            curl -H &quot;Authorization: Bearer mgm_…&quot;{" "}
            {typeof window === "undefined" ? "" : window.location.origin}/api/v1/proxy-hosts
          </code>
        ) : null}
      </CardContent>

      <CardFooter className="justify-end gap-2">
        {mode.kind === "idle" ? (
          <Button variant="outline" onClick={() => setMode({ kind: "create" })}>
            New API key
          </Button>
        ) : null}
        {mode.kind === "create" ? (
          <>
            <Button variant="ghost" onClick={reset} disabled={busy}>
              Cancel
            </Button>
            <Button
              onClick={() => void create()}
              disabled={busy || !name.trim() || (choice === "custom" && !customDate)}
            >
              {busy ? "Creating…" : "Create key"}
            </Button>
          </>
        ) : null}
        {mode.kind === "created" ? (
          // No Cancel and no dismissal: the only way out is acknowledging it,
          // because a stray click here costs the key.
          <Button onClick={reset} disabled={!acknowledged}>
            Done
          </Button>
        ) : null}
        {mode.kind === "delete" ? (
          <>
            <Button variant="ghost" onClick={reset} disabled={busy}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={() => void remove(mode.key)} disabled={busy}>
              {busy ? "Revoking…" : "Delete key"}
            </Button>
          </>
        ) : null}
      </CardFooter>
    </Card>
  );
}
