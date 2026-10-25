import type { Metadata } from "next";

import { LiveLogsView } from "@/components/live-logs/live-logs-view";
import { AdminOnly } from "@/components/admin-only";

export const metadata: Metadata = { title: "Live logs" };

export default function LiveLogsPage() {
  return (
    <AdminOnly>
      <LiveLogsView />
    </AdminOnly>
  );
}
