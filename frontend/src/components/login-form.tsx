"use client";

import { useState, type FormEvent } from "react";
import Image from "next/image";
import { useRouter, useSearchParams } from "next/navigation";

import { APP_NAME } from "@/lib/env";
import { ApiError } from "@/lib/api/errors";
import { useAuth } from "@/lib/auth/context";
import { DEFAULT_AUTHED_ROUTE, REDIRECT_PARAM } from "@/lib/auth/session";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/** Resolve the post-login destination, ignoring off-site `next` values. */
function safeRedirect(next: string | null): string {
  if (next && next.startsWith("/") && !next.startsWith("//")) {
    return next;
  }
  return DEFAULT_AUTHED_ROUTE;
}

/** Credential form that authenticates against the backend JWT endpoint. */
export function LoginForm() {
  const { login } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      router.replace(safeRedirect(searchParams.get(REDIRECT_PARAM)));
    } catch (err) {
      const message =
        err instanceof ApiError && err.status === 401
          ? "Incorrect email or password."
          : err instanceof ApiError
            ? err.detail
            : "Something went wrong. Please try again.";
      setError(message);
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center p-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="flex flex-col items-center gap-2 text-center">
          <Image
            src="/logo.png"
            alt={`${APP_NAME} logo`}
            width={64}
            height={64}
            priority
            className="size-16"
          />
          <h1 className="text-xl font-semibold">Sign in to {APP_NAME}</h1>
          <p className="text-muted-foreground text-sm">
            Enter your credentials to continue.
          </p>
        </div>

        <form className="space-y-3" onSubmit={onSubmit} noValidate>
          <Input
            type="email"
            name="email"
            placeholder="Email"
            autoComplete="email"
            aria-label="Email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={submitting}
          />
          <Input
            type="password"
            name="password"
            placeholder="Password"
            autoComplete="current-password"
            aria-label="Password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={submitting}
          />
          {error ? (
            <p role="alert" className="text-destructive text-sm">
              {error}
            </p>
          ) : null}
          <Button type="submit" className="w-full" disabled={submitting}>
            {submitting ? "Signing in…" : "Continue"}
          </Button>
        </form>
      </div>
    </div>
  );
}
