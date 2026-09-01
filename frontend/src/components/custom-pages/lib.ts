/**
 * Pure helpers for the Custom Pages editor.
 *
 * Kept free of React and of CodeMirror so the parts worth testing — sizing,
 * data-URI handling, image insertion — stay unit-testable without mounting an
 * editor. The editor itself is loaded dynamically and wires these in.
 */
import { MAX_PAGE_BYTES } from "@/lib/api";

export { describeError } from "@/components/proxy-hosts/lib";

/**
 * Above this, an inserted image is worth warning about: base64 inflates a file
 * by roughly a third, so a 256 KB photo adds ~340 KB to the source and makes
 * the document noticeably heavier to load and edit.
 */
export const IMAGE_WARN_BYTES = 256 * 1024;

/** Matches a base64 `data:` URI payload anywhere in a document. */
export const DATA_URI_PATTERN = /data:[\w.+-]+\/[\w.+-]+;base64,[A-Za-z0-9+/=]+/;

/** What a brand-new page starts from, so the editor is never a blank void. */
export const STARTER_HTML = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Untitled page</title>
    <style>
      body {
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        font: 16px/1.5 system-ui, sans-serif;
        color: #1c1c1c;
        background: #fafafa;
      }
      main { text-align: center; padding: 2rem; }
      h1 { margin: 0 0 0.5rem; font-size: 1.75rem; }
      p { margin: 0; color: #666; }
    </style>
  </head>
  <body>
    <main>
      <h1>Untitled page</h1>
      <p>Edit this page to say what you need it to say.</p>
    </main>
  </body>
</html>
`;

/** A human size for a byte count: "847 B", "4.2 KB", "2.0 MB". */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

/**
 * Encoded size of a document. The server's cap counts bytes, not characters,
 * so anything non-ASCII costs more than the string length suggests.
 */
export function htmlByteLength(html: string): number {
  return new TextEncoder().encode(html).length;
}

export function isOverPageCap(html: string): boolean {
  return htmlByteLength(html) > MAX_PAGE_BYTES;
}

/** Decoded size of a base64 `data:` URI's payload, or 0 if it is not one. */
export function base64Bytes(dataUri: string): number {
  const payload = dataUri.split(";base64,")[1];
  if (!payload) return 0;
  const padding = payload.endsWith("==") ? 2 : payload.endsWith("=") ? 1 : 0;
  return Math.max(0, (payload.length * 3) / 4 - padding);
}

/**
 * A warning for an image about to be embedded, or `null` when it is small
 * enough not to bother the user about.
 */
export function describeImageSize(bytes: number): string | null {
  if (bytes <= IMAGE_WARN_BYTES) return null;
  return (
    `That image is ${formatBytes(bytes)}. Embedding inflates it to about ` +
    `${formatBytes(Math.ceil((bytes * 4) / 3))}, and it becomes part of the ` +
    `page source — consider a smaller or more compressed file.`
  );
}

/** Escape a string for safe use inside a double-quoted HTML attribute. */
function escapeAttribute(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/**
 * The `<img>` tag inserted at the cursor. The filename (minus its extension)
 * becomes the alt text — a reasonable default the author can improve, and
 * better than shipping an image with no alt at all.
 */
export function imgTagFor(filename: string, dataUri: string): string {
  const alt = escapeAttribute(filename.replace(/\.[^.]+$/, ""));
  return `<img src="${dataUri}" alt="${alt}">`;
}

/**
 * The placeholder shown in place of a folded data URI. Without this a single
 * embedded image turns the source into an unreadable wall of base64.
 */
export function dataUriSummary(dataUri: string): string {
  const mime = dataUri.slice("data:".length).split(";")[0];
  return `data:${mime} (${formatBytes(base64Bytes(dataUri))})`;
}

export { MAX_PAGE_BYTES };
