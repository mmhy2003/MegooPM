"use client";

import { useCallback, useEffect, useState } from "react";
import { KeyRound, Trash2 } from "lucide-react";
import { startRegistration } from "@simplewebauthn/browser";
import type { PublicKeyCredentialCreationOptionsJSON } from "@simplewebauthn/browser";
import { toast } from "sonner";

import { users, type Passkey, type PasskeyRegister } from "@/lib/api";
import { fetchCapabilities } from "@/lib/auth/api";
import { classifyWebAuthnError, ORIGIN_MISMATCH_MESSAGE } from "@/lib/auth/webauthn";
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

const MAX_PASSKEYS = 10;

type Mode = { kind: "idle" } | { kind: "add" } | { kind: "remove"; passkey: Passkey };

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/**
 * Registered passkeys, and the two flows that change them. Rendered only when
 * 2FA is on and the backend can act as a relying party; otherwise nothing —
 * an option that cannot work is worse than none.
 */
export function PasskeysCard({ enabled }: { enabled: boolean }) {
  const [available, setAvailable] = useState(false);
  const [passkeys, setPasskeys] = useState<Passkey[]>([]);
  const [mode, setMode] = useState<Mode>({ kind: "idle" });
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    fetchCapabilities()
      .then((caps) => {
        if (active) setAvailable(caps.passkeys);
      })
      .catch(() => {
        // Leave it hidden; the rest of the page reports the real error.
      });
    return () => {
      active = false;
    };
  }, []);

  const load = useCallback(async () => {
    try {
      setPasskeys(await users.passkeys());
    } catch (err) {
      setError(describeError(err).message);
    }
  }, []);

  // The first load lives in the effect as a promise chain rather than a call
  // to `load`, so no state is set synchronously inside the effect body.
  useEffect(() => {
    if (!enabled || !available) return;
    let active = true;
    users
      .passkeys()
      .then((rows) => {
        if (active) setPasskeys(rows);
      })
      .catch((err: unknown) => {
        if (active) setError(describeError(err).message);
      });
    return () => {
      active = false;
    };
  }, [enabled, available]);

  function reset() {
    setMode({ kind: "idle" });
    setCode("");
    setName("");
    setError(null);
    setBusy(false);
  }

  async function add() {
    setError(null);
    setNote(null);
    setBusy(true);
    try {
      const { nonce, options } = await users.passkeyOptions(code);
      let credential: PasskeyRegister["credential"];
      try {
        credential = (await startRegistration({
          optionsJSON: options as unknown as PublicKeyCredentialCreationOptionsJSON,
        })) as unknown as PasskeyRegister["credential"];
      } catch (err) {
        const kind = classifyWebAuthnError(err);
        if (kind === "cancelled") {
          setNote("No passkey was added.");
          reset();
          return;
        }
        setError(
          kind === "origin"
            ? ORIGIN_MISMATCH_MESSAGE
            : kind === "unsupported"
              ? "This browser does not support passkeys."
              : "The passkey could not be created. Try again.",
        );
        setBusy(false);
        return;
      }
      await users.registerPasskey({ nonce, name, credential });
      toast.success("Passkey added");
      reset();
      await load();
    } catch (err) {
      setError(describeError(err).message);
      setBusy(false);
    }
  }

  async function remove(passkey: Passkey) {
    setError(null);
    setBusy(true);
    try {
      await users.removePasskey(passkey.id, code);
      toast.success("Passkey removed");
      reset();
      await load();
    } catch (err) {
      setError(describeError(err).message);
      setBusy(false);
    }
  }

  if (!enabled || !available) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <KeyRound className="size-4" /> Passkeys
        </CardTitle>
        <CardDescription>
          Sign in with Touch ID, Windows Hello, your phone, or a security key instead of typing a
          code. Your authenticator app and recovery codes keep working.
        </CardDescription>
      </CardHeader>

      <CardContent className="grid gap-4">
        {passkeys.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            No passkeys yet. A passkey is a key stored on a device you own; the device asks you to
            unlock it, and that is the whole sign-in step.
          </p>
        ) : (
          <ul className="divide-y rounded-lg border">
            {passkeys.map((p) => (
              <li key={p.id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                <div>
                  <div className="font-medium">{p.name}</div>
                  <div className="text-muted-foreground text-xs">
                    Added {formatDate(p.created_at)} ·{" "}
                    {p.last_used_at ? `Last used ${formatDate(p.last_used_at)}` : "Never used"}
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Remove ${p.name}`}
                  onClick={() => {
                    setNote(null);
                    setMode({ kind: "remove", passkey: p });
                  }}
                  disabled={mode.kind !== "idle"}
                >
                  <Trash2 />
                </Button>
              </li>
            ))}
          </ul>
        )}

        {mode.kind !== "idle" ? (
          <div className="grid gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="passkey-code">Code</Label>
              <Input
                id="passkey-code"
                autoComplete="one-time-code"
                placeholder="From your app, or a recovery code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                disabled={busy}
                autoFocus
              />
              <p className="text-muted-foreground text-xs">
                {mode.kind === "add"
                  ? "A code is required to add a passkey, so a stolen session cannot."
                  : `Removing ${mode.passkey.name}. A code is required.`}
              </p>
            </div>
            {mode.kind === "add" ? (
              <div className="space-y-1.5">
                <Label htmlFor="passkey-name">Name</Label>
                <Input
                  id="passkey-name"
                  placeholder="This MacBook"
                  maxLength={64}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  disabled={busy}
                />
              </div>
            ) : null}
          </div>
        ) : null}

        {note ? <p className="text-muted-foreground text-sm">{note}</p> : null}
        {error ? (
          <p role="alert" className="text-destructive text-sm">
            {error}
          </p>
        ) : null}
      </CardContent>

      <CardFooter className="justify-end gap-2">
        {mode.kind === "idle" ? (
          <Button
            variant="outline"
            onClick={() => {
              setNote(null);
              setMode({ kind: "add" });
            }}
            disabled={passkeys.length >= MAX_PASSKEYS}
          >
            <KeyRound /> Add a passkey
          </Button>
        ) : (
          <>
            <Button variant="outline" onClick={reset} disabled={busy}>
              Cancel
            </Button>
            {mode.kind === "add" ? (
              <Button onClick={() => void add()} disabled={busy || !code}>
                {busy ? "Waiting for your device…" : "Continue"}
              </Button>
            ) : (
              <Button
                variant="destructive"
                onClick={() => void remove(mode.passkey)}
                disabled={busy || !code}
              >
                Remove
              </Button>
            )}
          </>
        )}
      </CardFooter>
    </Card>
  );
}
