import type { Metadata } from "next";
import { ShieldAlert } from "lucide-react";

import { PagePlaceholder } from "@/components/page-placeholder";

export const metadata: Metadata = { title: "Security" };

export default function SecurityPage() {
  return (
    <PagePlaceholder
      title="Security"
      description="CrowdSec integration, bouncers and blocklists."
      icon={ShieldAlert}
    />
  );
}
