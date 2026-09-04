/**
 * Generate the logo the nginx-served pages inline.
 *
 * Run by hand after the logo changes; the output is committed, so a deploy
 * never depends on this script or on `sharp`:
 *
 *   node scripts/generate-nginx-logo.mjs
 *
 * 64px, because it is embedded as base64 into *ten* documents (eight error
 * pages, the default site, the ban page) — the 512px original would add a
 * quarter megabyte to the config directory on every apply.
 */
import path from "node:path";
import { fileURLToPath } from "node:url";

import sharp from "sharp";

const here = path.dirname(fileURLToPath(import.meta.url));
const SOURCE = path.join(here, "..", "public", "logo.png");
const OUT = path.join(
  here,
  "..",
  "..",
  "backend",
  "app",
  "templates",
  "nginx",
  "assets",
  "logo.png",
);
const SIZE = 64;

await sharp(SOURCE)
  .resize(SIZE, SIZE, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
  .png({ compressionLevel: 9 })
  .toFile(OUT);

console.log(`Wrote ${OUT}`);
