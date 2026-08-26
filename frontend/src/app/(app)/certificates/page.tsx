import type { Metadata } from "next";
import { ShieldCheck } from "lucide-react";

import { PagePlaceholder } from "@/components/page-placeholder";

export const metadata: Metadata = { title: "Certificates" };

export default function CertificatesPage() {
  return (
    <PagePlaceholder
      title="Certificates"
      description="TLS certificates and Let's Encrypt automation."
      icon={ShieldCheck}
    />
  );
}
