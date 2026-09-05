import type { Metadata } from "next";

import { AdminOnly } from "@/components/admin-only";
import { SettingsView } from "@/components/settings/settings-view";

export const metadata: Metadata = { title: "Settings" };

export default function SettingsPage() {
  return (
    <AdminOnly>
      <SettingsView />
    </AdminOnly>
  );
}
