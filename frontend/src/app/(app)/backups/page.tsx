import type { Metadata } from "next";

import { AdminOnly } from "@/components/admin-only";
import { BackupsView } from "@/components/backups/backups-view";

export const metadata: Metadata = { title: "Backups" };

export default function BackupsPage() {
  return (
    <AdminOnly>
      <BackupsView />
    </AdminOnly>
  );
}
