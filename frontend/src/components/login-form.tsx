"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Image from "next/image";
import { useRouter, useSearchParams } from "next/navigation";

import { APP_NAME } from "@/lib/env";
import { ApiError } from "@/lib/api/errors";
import { useAuth } from "@/lib/auth/context";
import { DEFAULT_AUTHED_ROUTE, REDIRECT_PARAM } from "@/lib/auth/session";
import {
  forgetAccount,
  readAccounts,
  type RecentAccount,
} from "@/lib/auth/recent-accounts";
import { AccountList } from "@/components/login/account-list";
import { ModeToggle } from "@/components/mode-toggle";
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

  // Accounts that signed in on this browser. Empty until the effect below runs:
  // `localStorage` does not exist on the server, so reading it during render
  // would make the first client paint disagree with the server's HTML.
  const [accounts, setAccounts] = useState<RecentAccount[]>([]);

  const emailRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);

  // One post-mount pass: load the remembered accounts, prefill the most recent
  // address, and put the caret on the only field a returning user must fill.
  // The focus target depends on state that does not exist until after mount,
  // which is why this is a ref call and not the `autoFocus` attribute.
  useEffect(() => {
    const stored = readAccounts();
    /* eslint-disable react-hooks/set-state-in-effect -- reading a browser-only
       store into state is exactly what a post-mount effect is for; doing it
       during render is the hydration mismatch this avoids. */
    if (stored.length > 0) {
      setAccounts(stored);
      setEmail(stored[0].email);
      passwordRef.current?.focus();
    } else {
      emailRef.current?.focus();
    }
    /* eslint-enable react-hooks/set-state-in-effect */
  }, []);

  function selectAccount(account: RecentAccount) {
    setEmail(account.email);
    setError(null);
    passwordRef.current?.focus();
  }

  function useAnotherAccount() {
    setEmail("");
    setError(null);
    emailRef.current?.focus();
  }

  function removeAccount(target: string) {
    forgetAccount(target);
    setAccounts(readAccounts());
    // Clear the box only when it held the address just forgotten; leaving it
    // would be the opposite of what Remove promises.
    if (email === target) setEmail("");
  }

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

  const hasAccounts = accounts.length > 0;

  return (
    <div className="flex min-h-dvh items-center justify-center p-4">
      {/* Fixed, and a sibling of the layout branch below rather than inside it:
          it must survive both the one- and two-column arrangements, and sit
          where the topbar's toggle sits once signed in so the position carries
          over instead of having to be found again. */}
      <div className="fixed top-4 right-4 z-10">
        <ModeToggle />
      </div>

      {/* Without saved accounts this collapses to the single centred column the
          page has always been. With them it becomes two columns only from `sm`
          up: on a phone the list stacks above the form rather than sitting
          beside it, where it would be a wall between the user and the fields. */}
      <div
        className={
          hasAccounts
            ? "grid w-full max-w-3xl gap-8 sm:grid-cols-[minmax(0,15rem)_1fr] sm:items-center"
            : "w-full max-w-sm"
        }
      >
        {hasAccounts ? (
          <AccountList
            accounts={accounts}
            selectedEmail={email}
            onSelect={selectAccount}
            onForget={removeAccount}
            onUseAnother={useAnotherAccount}
          />
        ) : null}

        <div className="w-full space-y-6 justify-self-center sm:max-w-sm">
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
              ref={emailRef}
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
              ref={passwordRef}
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
    </div>
  );
}
