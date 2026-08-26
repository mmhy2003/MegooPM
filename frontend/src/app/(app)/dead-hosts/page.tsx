import type { Metadata } from "next";

import { DeadHostsView } from "@/components/dead-hosts/dead-hosts-view";

export const metadata: Metadata = { title: "404 Hosts" };

export default function DeadHostsPage() {
  return <DeadHostsView />;
}
