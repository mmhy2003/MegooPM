"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api/errors";
import { resetPassword } from "@/lib/auth/api";
import { FORGOT_PASSWORD_ROUTE, LOGIN_ROUTE } from "@/lib/auth/session";
import { validateNewPassword } from "@/components/users/lib";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/**
 * The page the emailed link lands on.
 *
 * On success it sends the user to sign in rather than signing them in: the
 * token arrived by email, and spending it to mint a session would extend the
 * trust placed in that mailbox one step further than it needs to go.
 */
export function ResetPasswordForm() {
  const token = useSearchParams().get("token");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refused, setRefused] = useState(false);
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
      await resetPassword(token, password);
      setDone(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) {
        setRefused(true);
        setError(err.detail);
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
          <h1 className="text-xl font-semibold">Choose a new password</h1>
        </div>

        {!token ? (
          <p className="rounded-lg border p-4 text-sm">
            This link is incomplete. Open the link from your email again, or{" "}
            <Link
              href={FORGOT_PASSWORD_ROUTE}
              className="text-primary underline-offset-4 hover:underline"
            >
              request a new link
            </Link>
            .
          </p>
        ) : done ? (
          <div className="space-y-3 rounded-lg border p-4 text-sm">
            <p>Your password has been changed and every other session was signed out.</p>
            <Link href={LOGIN_ROUTE} className="text-primary underline-offset-4 hover:underline">
              Sign in
            </Link>
          </div>
        ) : (
          <form className="space-y-3" onSubmit={onSubmit} noValidate>
            <Input
              type="password"
              name="new-password"
              placeholder="New password"
              autoComplete="new-password"
              aria-label="New password"
              required
              autoFocus
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
                {refused ? (
                  <>
                    {" "}
                    <Link
                      href={FORGOT_PASSWORD_ROUTE}
                      className="text-primary underline-offset-4 hover:underline"
                    >
                      Request a new link
                    </Link>
                    .
                  </>
                ) : null}
              </p>
            ) : null}
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? "Saving…" : "Set new password"}
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
