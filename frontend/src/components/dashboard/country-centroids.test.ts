import { describe, expect, it } from "vitest";

import {
  COUNTRY_CENTROIDS,
  centroidFor,
} from "@/components/dashboard/country-centroids";

describe("centroidFor", () => {
  it("places a known country", () => {
    const point = centroidFor("DE");
    expect(point).not.toBeNull();
    // Germany: roughly 51N, 10E. Loose bounds — this guards against a
    // transposed pair, which would put it in the Indian Ocean.
    expect(point![0]).toBeGreaterThan(45);
    expect(point![0]).toBeLessThan(56);
    expect(point![1]).toBeGreaterThan(5);
    expect(point![1]).toBeLessThan(16);
  });

  it("places a southern-hemisphere country below the equator", () => {
    // A dropped minus sign is as easy to make as a transposition and just as
    // invisible: Australia would appear in Mongolia.
    expect(centroidFor("AU")![0]).toBeLessThan(0);
    expect(centroidFor("BR")![0]).toBeLessThan(0);
  });

  it("places a western-hemisphere country at a negative longitude", () => {
    expect(centroidFor("US")![1]).toBeLessThan(0);
  });

  it("normalises case", () => {
    // Country codes reach this from two sources and only one upper-cases them.
    expect(centroidFor("de")).toEqual(centroidFor("DE"));
  });

  it("returns null for a country it does not know", () => {
    expect(centroidFor("ZZ")).toBeNull();
  });

  it("returns null for junk rather than throwing", () => {
    expect(centroidFor("")).toBeNull();
    expect(centroidFor("NOT-A-CODE")).toBeNull();
  });

  it("keeps every entry inside real coordinate bounds", () => {
    // A transposed or mistyped pair is the likeliest error in hand-written
    // data, and it is invisible on a globe until someone notices Brazil in the
    // Pacific.
    for (const [code, [lat, lng]] of Object.entries(COUNTRY_CENTROIDS)) {
      expect(Math.abs(lat), `${code} latitude`).toBeLessThanOrEqual(90);
      expect(Math.abs(lng), `${code} longitude`).toBeLessThanOrEqual(180);
    }
  });

  it("uses two-letter uppercase keys throughout", () => {
    // Lookups upper-case their input, so a lower-case key would be unreachable.
    for (const code of Object.keys(COUNTRY_CENTROIDS)) {
      expect(code).toMatch(/^[A-Z]{2}$/);
    }
  });
});
