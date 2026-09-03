"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { APP_NAME } from "@/lib/env";
import { ApiError } from "@/lib/api/errors";
import { acceptInvite } from "@/lib/auth/api";
import { LOGIN_ROUTE } from "@/lib/auth/session";
import { validateNewPassword } from "@/components/users/lib";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/**
 * The page the invitation email lands on.
 *
 * A refused token points at an administrator, not at a resend: the only
 * address an invitation could be resent to is the one the person holding the
 * link already controls.
 */
export function AcceptInviteForm() {
  const token = useSearchParams().get("token");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    const problem = validateNewPassword(password, confirm);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await acceptInvite(token, fullName.trim(), password);
      setDone(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) {
        setError(`${err.detail} Ask an administrator to send you a new invitation.`);
      } else if (err instanceof ApiError && err.status === 429) {
        setError("Too many attempts. Please wait a while and try again.");
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center p-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="space-y-1 text-center">
          <h1 className="text-xl font-semibold">Join {APP_NAME}</h1>
          <p className="text-muted-foreground text-sm">Choose your name and a password.</p>
        </div>

        {!token ? (
          <p className="rounded-lg border p-4 text-sm">
            This link is incomplete. Open the link from your email again, or ask an
            administrator to send a new invitation.
          </p>
        ) : done ? (
          <div className="space-y-3 rounded-lg border p-4 text-sm">
            <p>Your account is ready.</p>
            <Link href={LOGIN_ROUTE} className="text-primary underline-offset-4 hover:underline">
              Sign in
            </Link>
          </div>
        ) : (
          <form className="space-y-3" onSubmit={onSubmit} noValidate>
            <Input
              name="full-name"
              placeholder="Full name"
              autoComplete="name"
              aria-label="Full name"
              autoFocus
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              disabled={submitting}
            />
            <Input
              type="password"
              name="new-password"
              placeholder="Password"
              autoComplete="new-password"
              aria-label="Password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={submitting}
            />
            <Input
              type="password"
              name="confirm-password"
              placeholder="Confirm password"
              autoComplete="new-password"
              aria-label="Confirm password"
              required
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              disabled={submitting}
            />
            {error ? (
              <p role="alert" className="text-destructive text-sm">
                {error}
              </p>
            ) : null}
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? "Saving…" : "Accept invitation"}
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
