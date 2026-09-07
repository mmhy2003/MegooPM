import type { Metadata } from "next";

import { RedirectionHostsView } from "@/components/redirection-hosts/redirection-hosts-view";
import { AdminOnly } from "@/components/admin-only";

export const metadata: Metadata = { title: "Redirection Hosts" };

export default function RedirectionHostsPage() {
  return (
    <AdminOnly>
      <RedirectionHostsView />
    </AdminOnly>
  );
}
