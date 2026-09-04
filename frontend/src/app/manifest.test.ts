import { describe, expect, it } from "vitest";

import manifest from "@/app/manifest";

const m = manifest();

describe("web app manifest", () => {
  it("carries what a browser requires before it offers to install", () => {
    // Chrome's installability bar: a name, a start URL, a standalone-ish
    // display mode, and icons at 192 and 512. Miss one and the install
    // prompt silently never appears, with nothing in the UI to explain it.
    expect(m.name).toBe("MegooPM");
    expect(m.short_name).toBe("MegooPM");
    expect(m.start_url).toBe("/");
    expect(m.display).toBe("standalone");

    const sizes = (m.icons ?? []).map((i) => i.sizes);
    expect(sizes).toContain("192x192");
    expect(sizes).toContain("512x512");
    for (const icon of m.icons ?? []) {
      expect(icon.type).toBe("image/png");
      expect(icon.src.startsWith("/")).toBe(true);
    }
  });

  it("ships a maskable icon at both sizes", () => {
    // Android crops any non-maskable icon into its mask, which eats a logo
    // that runs to the edges. The padded variants exist for that.
    const maskable = (m.icons ?? []).filter((i) => i.purpose === "maskable");
    expect(maskable.map((i) => i.sizes).sort()).toEqual(["192x192", "512x512"]);
  });

  it("uses the app's own background colour, not a browser default", () => {
    expect(m.background_color).toBe("#f0f9fb");
    expect(m.theme_color).toBe("#007789");
  });
});
