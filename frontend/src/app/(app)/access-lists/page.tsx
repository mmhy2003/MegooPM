import type { Metadata } from "next";
import { ListChecks } from "lucide-react";

import { PagePlaceholder } from "@/components/page-placeholder";

export const metadata: Metadata = { title: "Access Lists" };

export default function AccessListsPage() {
  return (
    <PagePlaceholder
      title="Access Lists"
      description="Authorization rules controlling who can reach a host."
      icon={ListChecks}
    />
  );
}
