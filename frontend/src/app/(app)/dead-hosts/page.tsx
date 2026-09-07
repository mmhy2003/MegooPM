import type { Metadata } from "next";

import { DeadHostsView } from "@/components/dead-hosts/dead-hosts-view";
import { AdminOnly } from "@/components/admin-only";

export const metadata: Metadata = { title: "404 Hosts" };

export default function DeadHostsPage() {
  return (
    <AdminOnly>
      <DeadHostsView />
    </AdminOnly>
  );
}
