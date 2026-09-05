"use client";

/**
 * A custom page rendered as it will be served.
 *
 * `sandbox="allow-scripts"` without `allow-same-origin` puts the frame on an
 * opaque origin: scripts in the page still run, so the preview is faithful,
 * but they can never reach into the admin app's DOM, storage or cookies.
 *
 * Shared by the editor's live pane and the list's preview dialog, so that
 * guarantee is written once. Do not inline a second iframe elsewhere: page
 * HTML is arbitrary, and a copy that loses `sandbox` is a stored-XSS foothold
 * in the admin origin.
 */
export function PagePreview({ html, className }: { html: string; className?: string }) {
  return (
    <iframe title="Page preview" sandbox="allow-scripts" srcDoc={html} className={className} />
  );
}
