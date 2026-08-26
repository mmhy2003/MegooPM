import type { Metadata } from "next";
import { Boxes } from "lucide-react";

import { APP_NAME } from "@/lib/env";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export const metadata: Metadata = { title: "Sign in" };

/**
 * Login skeleton. The form is intentionally inert at the foundation stage —
 * the auth ticket wires it to the backend token endpoint and sets the session
 * cookie consumed by the middleware.
 */
export default function LoginPage() {
  return (
    <div className="flex min-h-dvh items-center justify-center p-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="flex flex-col items-center gap-2 text-center">
          <div className="bg-primary text-primary-foreground flex size-11 items-center justify-center rounded-xl">
            <Boxes className="size-6" />
          </div>
          <h1 className="text-xl font-semibold">Sign in to {APP_NAME}</h1>
          <p className="text-muted-foreground text-sm">
            Authentication is not wired up yet.
          </p>
        </div>

        <form className="space-y-3">
          <Input type="email" placeholder="Email" autoComplete="email" disabled />
          <Input
            type="password"
            placeholder="Password"
            autoComplete="current-password"
            disabled
          />
          <Button type="submit" className="w-full" disabled>
            Continue
          </Button>
        </form>
      </div>
    </div>
  );
}
