import type { Metadata } from "next";
import { Suspense } from "react";

import { AcceptInviteForm } from "@/components/auth/accept-invite-form";

export const metadata: Metadata = { title: "Accept invitation" };

/** Suspense because the form reads the token from the query string. */
export default function AcceptInvitePage() {
  return (
    <Suspense>
      <AcceptInviteForm />
    </Suspense>
  );
}
