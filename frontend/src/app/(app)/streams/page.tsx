import type { Metadata } from "next";
import { Network } from "lucide-react";

import { PagePlaceholder } from "@/components/page-placeholder";

export const metadata: Metadata = { title: "Streams" };

export default function StreamsPage() {
  return (
    <PagePlaceholder
      title="Streams"
      description="Raw TCP/UDP stream forwarding."
      icon={Network}
    />
  );
}
