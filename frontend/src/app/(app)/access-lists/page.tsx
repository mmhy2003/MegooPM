import type { Metadata } from "next";

import { AccessListsView } from "@/components/access-lists/access-lists-view";

export const metadata: Metadata = { title: "Access Lists" };

export default function AccessListsPage() {
  return <AccessListsView />;
}
