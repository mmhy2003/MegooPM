import type { Metadata } from "next";

import { ProxyHostsView } from "@/components/proxy-hosts/proxy-hosts-view";

export const metadata: Metadata = { title: "Proxy Hosts" };

export default function ProxyHostsPage() {
  return <ProxyHostsView />;
}
