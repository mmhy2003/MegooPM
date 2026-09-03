"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api/errors";
import { requestPasswordReset } from "@/lib/auth/api";
import { LOGIN_ROUTE } from "@/lib/auth/session";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const NEUTRAL = "If that address is registered, a reset link is on its way.";

/**
 * Ask for a reset link.
 *
 * Shows one message whatever happened. The backend never says whether the
 * address exists, and this page must not become a second oracle on top of it.
 */
export function ForgotPasswordForm() {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await requestPasswordReset(email);
      setDone(true);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 429
          ? "Too many requests. Please wait a while and try again."
          : err instanceof ApiError
            ? err.detail
            : "Something went wrong. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center p-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="space-y-1 text-center">
          <h1 className="text-xl font-semibold">Forgot your password?</h1>
          <p className="text-muted-foreground text-sm">
            Enter your email and we&apos;ll send you a link to choose a new one.
          </p>
        </div>

        {done ? (
          <p className="rounded-lg border p-4 text-sm">{NEUTRAL}</p>
        ) : (
          <form className="space-y-3" onSubmit={onSubmit} noValidate>
            <Input
              type="email"
              name="email"
              placeholder="Email"
              autoComplete="email"
              aria-label="Email"
              required
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={submitting}
            />
            {error ? (
              <p role="alert" className="text-destructive text-sm">
                {error}
              </p>
            ) : null}
            <Button type="submit" className="w-full" disabled={submitting || !email}>
              {submitting ? "Sending…" : "Send reset link"}
            </Button>
          </form>
        )}

        <p className="text-center text-sm">
          <Link href={LOGIN_ROUTE} className="text-primary underline-offset-4 hover:underline">
            Back to sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
