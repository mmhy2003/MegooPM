import { describe, expect, it } from "vitest";

import { HOME_ROUTE, primaryNav } from "@/config/nav";

describe("primaryNav", () => {
  it("covers every MegooPM product area", () => {
    const titles = primaryNav.map((item) => item.title);
    expect(titles).toEqual([
      "Proxy Hosts",
      "Certificates",
      "Access Lists",
      "Streams",
      "Redirection Hosts",
      "404 Hosts",
      "Security",
    ]);
  });

  it("uses absolute, unique hrefs", () => {
    const hrefs = primaryNav.map((item) => item.href);
    expect(hrefs.every((href) => href.startsWith("/"))).toBe(true);
    expect(new Set(hrefs).size).toBe(hrefs.length);
  });

  it("points the home route at a real nav destination", () => {
    expect(primaryNav.some((item) => item.href === HOME_ROUTE)).toBe(true);
  });
});
