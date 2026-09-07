import type { Metadata } from "next";

import { SecurityView } from "@/components/security/security-view";
import { AdminOnly } from "@/components/admin-only";

export const metadata: Metadata = { title: "Security" };

export default function SecurityPage() {
  return (
    <AdminOnly>
      <SecurityView />
    </AdminOnly>
  );
}
