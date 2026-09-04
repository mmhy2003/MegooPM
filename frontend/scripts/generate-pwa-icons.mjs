/**
 * Generate the manifest's PNG icons from `public/logo.png`.
 *
 * Run by hand after the logo changes; the outputs are committed, so a deploy
 * never depends on this script or on `sharp` being installable in the image:
 *
 *   node scripts/generate-pwa-icons.mjs
 *
 * Two shapes per size. The "any" icons are the logo scaled to fill. The
 * "maskable" icons put the logo in the safe zone — the middle 80% of the
 * canvas — because Android crops a maskable icon into a circle or squircle,
 * and a logo that runs to the edges loses its corners.
 */
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import sharp from "sharp";

const here = path.dirname(fileURLToPath(import.meta.url));
const SOURCE = path.join(here, "..", "public", "logo.png");
const OUT = path.join(here, "..", "public");

/** The manifest's `background_color`, so padding matches the splash screen. */
const BACKGROUND = "#f0f9fb";
const SIZES = [192, 512];
/** Android's maskable safe zone: the logo occupies the middle 80%. */
const SAFE_ZONE = 0.8;

async function main() {
  await mkdir(OUT, { recursive: true });

  for (const size of SIZES) {
    await sharp(SOURCE)
      .resize(size, size, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
      .png()
      .toFile(path.join(OUT, `icon-${size}.png`));

    const inner = Math.round(size * SAFE_ZONE);
    const pad = Math.round((size - inner) / 2);
    const logo = await sharp(SOURCE)
      .resize(inner, inner, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
      .png()
      .toBuffer();
    await sharp({
      create: { width: size, height: size, channels: 4, background: BACKGROUND },
    })
      .composite([{ input: logo, top: pad, left: pad }])
      .png()
      .toFile(path.join(OUT, `icon-maskable-${size}.png`));
  }

  console.log(`Wrote ${SIZES.length * 2} icons to public/`);
}

await main();
