"use client";

import { useState } from "react";
import { Copy, ShieldCheck, ShieldOff } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { toast } from "sonner";

import { users, type TotpSetup } from "@/lib/api";
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

type Mode =
  | { kind: "idle" }
  | { kind: "setup"; setup: TotpSetup }
  | { kind: "codes"; codes: string[]; reason: "enabled" | "regenerated" }
  | { kind: "ask"; action: "disable" | "regenerate" };

/** Group a base32 secret in fours for reading off a screen into a phone. */
function grouped(secret: string): string {
  return secret.match(/.{1,4}/g)?.join(" ") ?? secret;
}

/**
 * Two-factor authentication, in four states: off, setting up, showing the
 * one-time recovery codes, and on.
 *
 * `enabled` comes from the session user; `onChanged` asks the parent to
 * refresh it, so the card never guesses the server's state.
 */
export function TotpCard({ enabled, onChanged }: { enabled: boolean; onChanged: () => void }) {
  const [mode, setMode] = useState<Mode>({ kind: "idle" });
  const [code, setCode] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function reset() {
    setMode({ kind: "idle" });
    setCode("");
    setAcknowledged(false);
    setError(null);
  }

  async function run(fn: () => Promise<void>) {
    setError(null);
    setBusy(true);
    try {
      await fn();
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setBusy(false);
    }
  }

  const start = () =>
    run(async () => {
      setMode({ kind: "setup", setup: await users.totpSetup() });
    });

  const confirm = () =>
    run(async () => {
      const { codes } = await users.totpEnable(code);
      setCode("");
      setMode({ kind: "codes", codes, reason: "enabled" });
      onChanged();
    });

  const disable = () =>
    run(async () => {
      await users.totpDisable(code);
      toast.success("Two-factor authentication turned off");
      reset();
      onChanged();
    });

  const regenerate = () =>
    run(async () => {
      const { codes } = await users.totpRegenerate(code);
      setCode("");
      setMode({ kind: "codes", codes, reason: "regenerated" });
    });

  async function copyCodes(codes: string[]) {
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      toast.success("Recovery codes copied");
    } catch {
      toast.error("Could not copy. Select the codes and copy them by hand.");
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          {enabled ? (
            <ShieldCheck className="size-4 text-success" />
          ) : (
            <ShieldOff className="size-4 text-muted-foreground" />
          )}
          Two-factor authentication
        </CardTitle>
        <CardDescription>
          {enabled
            ? "Signing in needs your password and a code from your authenticator app."
            : "Add a second step to signing in, using an authenticator app on your phone."}
        </CardDescription>
      </CardHeader>

      <CardContent className="grid gap-4">
        {mode.kind === "setup" ? (
          <>
            <div className="flex flex-col items-center gap-3 sm:flex-row sm:items-start">
              {/* White on purpose, in both themes: phone cameras read a dark
                  QR on a light ground far more reliably than the inverse. */}
              <div className="rounded-lg border bg-white p-3">
                <QRCodeSVG value={mode.setup.otpauth_uri} size={160} />
              </div>
              <div className="space-y-2 text-sm">
                <p>Scan this with your authenticator app, or enter the key by hand:</p>
                <code className="block rounded bg-muted px-2 py-1 font-mono text-xs tracking-wider">
                  {grouped(mode.setup.secret)}
                </code>
                <p className="text-muted-foreground text-xs">
                  Then enter the six-digit code the app shows to confirm it works. Nothing is turned
                  on until you do.
                </p>
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="totp-confirm">Code from your app</Label>
              <Input
                id="totp-confirm"
                inputMode="numeric"
                autoComplete="one-time-code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                disabled={busy}
              />
            </div>
          </>
        ) : null}

        {mode.kind === "codes" ? (
          <div className="space-y-3">
            <p className="text-sm font-medium">
              {mode.reason === "enabled"
                ? "Two-factor authentication is on. Save these recovery codes."
                : "Your old recovery codes no longer work. Save these."}
            </p>
            <p className="text-muted-foreground text-sm">
              Each code signs you in once if you lose your phone. This is the{" "}
              <strong>only time</strong> they will be shown.
            </p>
            <ul className="grid grid-cols-2 gap-1 rounded-lg border bg-muted p-3 font-mono text-sm">
              {mode.codes.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
            <Button variant="outline" size="sm" onClick={() => void copyCodes(mode.codes)}>
              <Copy /> Copy all
            </Button>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(e) => setAcknowledged(e.target.checked)}
                aria-label="I have saved these codes"
              />
              I have saved these codes somewhere safe.
            </label>
          </div>
        ) : null}

        {mode.kind === "ask" ? (
          <div className="space-y-1.5">
            <Label htmlFor="totp-ask">Code</Label>
            <Input
              id="totp-ask"
              autoComplete="one-time-code"
              placeholder="From your app, or a recovery code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              disabled={busy}
            />
            <p className="text-muted-foreground text-xs">
              {mode.action === "disable"
                ? "A code is required to turn this off, so a stolen session cannot."
                : "A code is required. Your current recovery codes will stop working."}
            </p>
          </div>
        ) : null}

        {error ? (
          <p role="alert" className="text-destructive text-sm">
            {error}
          </p>
        ) : null}
      </CardContent>

      <CardFooter className="justify-end gap-2">
        {mode.kind === "idle" && !enabled ? (
          <Button onClick={() => void start()} disabled={busy}>
            Enable
          </Button>
        ) : null}
        {mode.kind === "idle" && enabled ? (
          <>
            <Button
              variant="outline"
              onClick={() => setMode({ kind: "ask", action: "regenerate" })}
            >
              Regenerate recovery codes
            </Button>
            <Button
              variant="destructive"
              onClick={() => setMode({ kind: "ask", action: "disable" })}
            >
              Disable
            </Button>
          </>
        ) : null}
        {mode.kind === "setup" ? (
          <>
            <Button variant="outline" onClick={reset} disabled={busy}>
              Cancel
            </Button>
            <Button onClick={() => void confirm()} disabled={busy || code.length < 6}>
              Confirm
            </Button>
          </>
        ) : null}
        {mode.kind === "codes" ? (
          <Button onClick={reset} disabled={!acknowledged}>
            Done
          </Button>
        ) : null}
        {mode.kind === "ask" ? (
          <>
            <Button variant="outline" onClick={reset} disabled={busy}>
              Cancel
            </Button>
            {mode.action === "disable" ? (
              <Button variant="destructive" onClick={() => void disable()} disabled={busy || !code}>
                Turn off
              </Button>
            ) : (
              <Button onClick={() => void regenerate()} disabled={busy || !code}>
                Generate new codes
              </Button>
            )}
          </>
        ) : null}
      </CardFooter>
    </Card>
  );
}
