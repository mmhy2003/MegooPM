import type { Metadata } from "next";

import { StreamsView } from "@/components/streams/streams-view";
import { AdminOnly } from "@/components/admin-only";

export const metadata: Metadata = { title: "Streams" };

export default function StreamsPage() {
  return (
    <AdminOnly>
      <StreamsView />
    </AdminOnly>
  );
}
