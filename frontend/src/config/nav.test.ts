import { describe, expect, it } from "vitest";

import { HOME_ROUTE, navForRole, primaryNav, utilityRoutes } from "@/config/nav";

describe("primaryNav", () => {
  it("covers every MegooPM product area", () => {
    const titles = primaryNav.map((item) => item.title);
    expect(titles).toEqual([
      "Proxy Hosts",
      "Upstream Pools",
      "Certificates",
      "Access Lists",
      "Streams",
      "Redirection Hosts",
      "404 Hosts",
      "Security",
      "Users",
      "Settings",
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

  it("marks only Users as admin-only", () => {
    const adminOnly = primaryNav.filter((item) => item.adminOnly).map((item) => item.href);
    expect(adminOnly).toEqual(["/users", "/settings"]);
  });
});

describe("navForRole", () => {
  it("shows admin-only items to admins", () => {
    expect(navForRole("admin").map((i) => i.href)).toContain("/users");
  });

  it("hides admin-only items from members and signed-out visitors", () => {
    expect(navForRole("member").map((i) => i.href)).not.toContain("/users");
    expect(navForRole(null).map((i) => i.href)).not.toContain("/users");
    expect(navForRole(undefined).map((i) => i.href)).not.toContain("/users");
  });

  it("keeps the public items and their order for every role", () => {
    const publicHrefs = primaryNav.filter((i) => !i.adminOnly).map((i) => i.href);
    expect(navForRole("member").map((i) => i.href)).toEqual(publicHrefs);
  });
});

describe("utilityRoutes", () => {
  it("names the profile page, which is not in the sidebar", () => {
    expect(utilityRoutes["/profile"]).toBe("Profile");
    expect(utilityRoutes["/account"]).toBeUndefined();
    expect(primaryNav.some((i) => i.href === "/profile")).toBe(false);
  });
});
