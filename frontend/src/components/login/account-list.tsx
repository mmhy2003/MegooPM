"use client";

import { X } from "lucide-react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { displayName, initials } from "@/components/users/lib";
import { cn } from "@/lib/utils";
import type { RecentAccount } from "@/lib/auth/recent-accounts";

/**
 * Accounts that have signed in on this browser, offered beside the login form.
 *
 * Renders nothing when the list is empty, so a browser that has never signed
 * in sees exactly the login page it saw before this existed.
 *
 * Rows are toggle buttons rather than links or radios: picking one sets form
 * state and navigates nowhere, and `aria-pressed` says which is active — the
 * same pattern the dashboard's layer toggle uses.
 */
export function AccountList({
  accounts,
  selectedEmail,
  onSelect,
  onForget,
  onUseAnother,
}: {
  accounts: RecentAccount[];
  selectedEmail: string | null;
  onSelect: (account: RecentAccount) => void;
  onForget: (email: string) => void;
  onUseAnother: () => void;
}) {
  if (accounts.length === 0) return null;

  return (
    <div className="space-y-2">
      <h2 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
        Accounts
      </h2>
      <ul className="space-y-1">
        {accounts.map((account) => {
          const name = displayName(account);
          const selected = account.email === selectedEmail;
          return (
            <li key={account.email} className="group/account relative">
              <button
                type="button"
                // Explicit, because the row's text content ("Mohamed Hammad
                // mm@example.com") names the account without saying what
                // pressing it does — and it would collide with "Remove …".
                aria-label={`Sign in as ${name}`}
                aria-pressed={selected}
                onClick={() => onSelect(account)}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-lg border p-2 pr-9 text-left transition-colors",
                  selected
                    ? "border-primary/40 bg-primary/5"
                    : "border-transparent hover:bg-muted",
                )}
              >
                <Avatar className="size-8 shrink-0">
                  <AvatarFallback className="bg-primary text-xs font-semibold text-primary-foreground">
                    {initials(account)}
                  </AvatarFallback>
                </Avatar>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{name}</span>
                  {/* The email even when a name exists: two people can share a
                      name, and the address is what actually signs in. */}
                  {name === account.email ? null : (
                    <span className="text-muted-foreground block truncate text-xs">
                      {account.email}
                    </span>
                  )}
                </span>
              </button>
              {/* Outside the account button, not nested inside it: a button
                  within a button is invalid, and the click would both remove
                  the account and select it on the way up. */}
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={`Remove ${name}`}
                onClick={() => onForget(account.email)}
                className="absolute top-1/2 right-1 -translate-y-1/2 opacity-0 transition-opacity group-hover/account:opacity-100 focus-visible:opacity-100"
              >
                <X />
              </Button>
            </li>
          );
        })}
      </ul>
      <Button
        type="button"
        variant="link"
        size="sm"
        onClick={onUseAnother}
        className="h-auto p-0 text-sm"
      >
        Use another account
      </Button>
    </div>
  );
}
