import type { Metadata } from "next";

import { StaticSitesView } from "@/components/static-sites/static-sites-view";
import { AdminOnly } from "@/components/admin-only";

export const metadata: Metadata = { title: "Static Sites" };

export default function StaticSitesPage() {
  return (
    <AdminOnly>
      <StaticSitesView />
    </AdminOnly>
  );
}
