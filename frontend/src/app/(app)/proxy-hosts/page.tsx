import type { Metadata } from "next";

import { ProxyHostsView } from "@/components/proxy-hosts/proxy-hosts-view";
import { AdminOnly } from "@/components/admin-only";

export const metadata: Metadata = { title: "Proxy Hosts" };

export default function ProxyHostsPage() {
  return (
    <AdminOnly>
      <ProxyHostsView />
    </AdminOnly>
  );
}
