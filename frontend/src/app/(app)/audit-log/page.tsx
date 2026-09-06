import type { Metadata } from "next";

import { AdminOnly } from "@/components/admin-only";
import { AuditLogView } from "@/components/audit-log/audit-log-view";

export const metadata: Metadata = { title: "Audit Log" };

export default function AuditLogPage() {
  return (
    <AdminOnly>
      <AuditLogView />
    </AdminOnly>
  );
}
