import type { Metadata } from "next";

import { AccessListsView } from "@/components/access-lists/access-lists-view";
import { AdminOnly } from "@/components/admin-only";

export const metadata: Metadata = { title: "Access Lists" };

export default function AccessListsPage() {
  return (
    <AdminOnly>
      <AccessListsView />
    </AdminOnly>
  );
}
