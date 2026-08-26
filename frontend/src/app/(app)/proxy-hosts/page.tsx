import type { Metadata } from "next";
import { Globe } from "lucide-react";

import { PagePlaceholder } from "@/components/page-placeholder";

export const metadata: Metadata = { title: "Proxy Hosts" };

export default function ProxyHostsPage() {
  return (
    <PagePlaceholder
      title="Proxy Hosts"
      description="Reverse-proxy hosts forwarding traffic to upstream services."
      icon={Globe}
    />
  );
}
