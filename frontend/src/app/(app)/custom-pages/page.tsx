import type { Metadata } from "next";

import { CustomPagesView } from "@/components/custom-pages/custom-pages-view";

export const metadata: Metadata = { title: "Custom Pages" };

export default function CustomPagesPage() {
  return <CustomPagesView />;
}
