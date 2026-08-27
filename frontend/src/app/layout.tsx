import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";

import { Providers } from "@/components/providers";
import { APP_NAME } from "@/lib/env";
import "./globals.css";

// UI typeface pairing: Inter for interface text, JetBrains Mono for hostnames,
// certificates, and rendered config. next/font self-hosts both at build time
// (no runtime requests to Google) and exposes each as a CSS variable on <html>;
// globals.css wires those variables into the font-sans / font-mono utilities.
const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: APP_NAME,
    template: `%s · ${APP_NAME}`,
  },
  description: "Self-hosted reverse-proxy management for hosts, certificates, and streams.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${inter.variable} ${jetbrainsMono.variable} h-full antialiased`}
    >
      <body className="min-h-full">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
