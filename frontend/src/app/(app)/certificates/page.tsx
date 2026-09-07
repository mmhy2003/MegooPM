import type { Metadata } from "next";

import { CertificatesView } from "@/components/certificates/certificates-view";
import { AdminOnly } from "@/components/admin-only";

export const metadata: Metadata = { title: "Certificates" };

export default function CertificatesPage() {
  return (
    <AdminOnly>
      <CertificatesView />
    </AdminOnly>
  );
}
