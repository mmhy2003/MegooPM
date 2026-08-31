import type { Metadata } from "next";
import { Settings } from "lucide-react";

import { PagePlaceholder } from "@/components/page-placeholder";

export const metadata: Metadata = { title: "Settings" };

export default function SettingsPage() {
  return (
    <PagePlaceholder
      title="Settings"
      description="Instance configuration."
      icon={Settings}
    />
  );
}
