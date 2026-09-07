import type { Metadata } from "next";

import { UpstreamsView } from "@/components/upstreams/upstreams-view";
import { AdminOnly } from "@/components/admin-only";

export const metadata: Metadata = { title: "Upstream Pools" };

export default function UpstreamsPage() {
  return (
    <AdminOnly>
      <UpstreamsView />
    </AdminOnly>
  );
}
