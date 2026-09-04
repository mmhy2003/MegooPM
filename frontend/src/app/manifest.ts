import type { MetadataRoute } from "next";

import { APP_NAME } from "@/lib/env";

/**
 * The web app manifest, served at `/manifest.webmanifest`.
 *
 * A route rather than a static JSON file so the name stays tied to
 * {@link APP_NAME} and the colours can be read against `globals.css` in one
 * place. The colours are the light palette's `--background` and `--primary`,
 * transcoded from oklch to hex: a manifest cannot carry oklch, and a browser
 * that fails to parse a colour falls back to white.
 *
 * `display: "standalone"` opens the installed app in its own window without
 * browser chrome. Installation needs a secure context — `localhost` over HTTP
 * is fine, anything else must be HTTPS or no install prompt appears.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: APP_NAME,
    short_name: APP_NAME,
    description: "Self-hosted reverse-proxy management for hosts, certificates, and streams.",
    start_url: "/",
    display: "standalone",
    background_color: "#f0f9fb",
    theme_color: "#007789",
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      // Padded so Android's circle/squircle mask crops the padding, not the
      // logo. A single icon marked "any maskable" would be cropped in one
      // context or letterboxed in the other, so these are separate files.
      { src: "/icon-maskable-192.png", sizes: "192x192", type: "image/png", purpose: "maskable" },
      { src: "/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
