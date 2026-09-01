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

/* -------------------------------------------------------------------------- */
/* The image round trip                                                        */
/* -------------------------------------------------------------------------- */

/**
 * The most an elided document may be before it is sent to a model. 2 MiB of
 * pure markup is unusual but possible, and there is no point paying for it.
 * The backend enforces the same number — this copy exists only so the operator
 * gets a sentence instead of a 422.
 */
export const MAX_ASSIST_BYTES = 200 * 1024;

/** Matches a whole data URI and captures its mime type. */
const DATA_URI_WITH_MIME = /data:([\w.+-]+\/[\w.+-]+);base64,[A-Za-z0-9+/=]+/g;

/** Matches a placeholder the model handed back, capturing its 1-based index. */
const PLACEHOLDER_URI = /data:[\w.+-]+\/[\w.+-]+;base64,MEGOOPM_IMAGE_(\d+)/g;

export type ElidedDocument = {
  /** The document with every data URI replaced by a placeholder. */
  html: string;
  /** The originals, in order; index `i` backs `MEGOOPM_IMAGE_{i + 1}`. */
  images: string[];
};

export type RestoredDocument = {
  html: string;
  /** Notes for the operator about images the model dropped or invented. */
  warnings: string[];
};

/**
 * Swap every embedded image for a placeholder before sending to a model.
 *
 * Base64 runs about a token per three characters, so one 200 KB screenshot
 * inside a page costs roughly 70k tokens of context for a blob the model cannot
 * read. The placeholder deliberately stays a *well-formed* data URI, mime type
 * and all: a model shown a malformed `src` attribute tends to repair it, while
 * one shown a URI it simply does not understand leaves it alone.
 */
export function elideImages(html: string): ElidedDocument {
  const images: string[] = [];
  const elided = html.replace(DATA_URI_WITH_MIME, (match, mime: string) => {
    images.push(match);
    return `data:${mime};base64,MEGOOPM_IMAGE_${images.length}`;
  });
  return { html: elided, images };
}

/** "1 image" / "2 images", so the notes below read like English. */
function countImages(n: number): string {
  return n === 1 ? "1 image" : `${n} images`;
}

/**
 * Put the real images back, and report what the model did with them.
 *
 * A *missing* placeholder means the model removed that image — legitimate, the
 * instruction may have asked for exactly that — so the result stands and the
 * operator is told how many went.
 *
 * A placeholder that was never sent is left in place on purpose. Stripping the
 * surrounding `<img>` would be a silent structural edit nobody asked for; a
 * broken image in the live preview is immediately visible and immediately
 * fixable.
 */
export function restoreImages(html: string, images: string[]): RestoredDocument {
  const seen = new Set<number>();
  let unknown = 0;

  const restored = html.replace(PLACEHOLDER_URI, (match, index: string) => {
    const position = Number(index);
    const original = images[position - 1];
    if (original === undefined) {
      unknown += 1;
      return match;
    }
    seen.add(position);
    return original;
  });

  const warnings: string[] = [];
  const dropped = images.length - seen.size;
  if (dropped > 0) {
    warnings.push(
      `${countImages(dropped)} ${dropped === 1 ? "was" : "were"} removed from the page.`,
    );
  }
  if (unknown > 0) {
    warnings.push(`The result referenced ${countImages(unknown)} that isn't in your page.`);
  }
  return { html: restored, warnings };
}

/** Whether an elided document is too large to send. */
export function isOverAssistCap(html: string): boolean {
  return htmlByteLength(html) > MAX_ASSIST_BYTES;
}
