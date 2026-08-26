import type { Metadata } from "next";

import { RedirectionHostsView } from "@/components/redirection-hosts/redirection-hosts-view";

export const metadata: Metadata = { title: "Redirection Hosts" };

export default function RedirectionHostsPage() {
  return <RedirectionHostsView />;
}
