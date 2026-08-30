import type { Metadata } from "next";

import { UpstreamsView } from "@/components/upstreams/upstreams-view";

export const metadata: Metadata = { title: "Upstream Pools" };

export default function UpstreamsPage() {
  return <UpstreamsView />;
}
