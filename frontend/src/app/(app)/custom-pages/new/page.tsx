import type { Metadata } from "next";

import { CustomPageEditorView } from "@/components/custom-pages/custom-page-editor-view";

export const metadata: Metadata = { title: "New custom page" };

/** Create mode: a static segment, so it wins over the sibling `[id]` route. */
export default function NewCustomPagePage() {
  return <CustomPageEditorView pageId={null} />;
}
