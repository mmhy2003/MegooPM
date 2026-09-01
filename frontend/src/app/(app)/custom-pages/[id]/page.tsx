import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { CustomPageEditorView } from "@/components/custom-pages/custom-page-editor-view";

export const metadata: Metadata = { title: "Edit custom page" };

/** `params` is a promise in this version of Next; await it before reading. */
export default async function CustomPageEditorPage(props: PageProps<"/custom-pages/[id]">) {
  const { id } = await props.params;
  const pageId = Number(id);
  if (!Number.isInteger(pageId) || pageId <= 0) notFound();
  return <CustomPageEditorView pageId={pageId} />;
}
