import type { Metadata } from "next";

import { CustomPagesView } from "@/components/custom-pages/custom-pages-view";
import { AdminOnly } from "@/components/admin-only";

export const metadata: Metadata = { title: "Custom Pages" };

export default function CustomPagesPage() {
  return (
    <AdminOnly>
      <CustomPagesView />
    </AdminOnly>
  );
}
