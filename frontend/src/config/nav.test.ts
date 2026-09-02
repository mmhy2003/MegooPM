import { describe, expect, it } from "vitest";

import { HOME_ROUTE, navForRole, primaryNav, utilityRoutes } from "@/config/nav";
import { isActivePath } from "@/components/app-sidebar";

describe("primaryNav", () => {
  it("covers every MegooPM product area", () => {
    const titles = primaryNav.map((item) => item.title);
    expect(titles).toEqual([
      "Dashboard",
      "Proxy Hosts",
      "Upstream Pools",
      "Certificates",
      "Access Lists",
      "Streams",
      "Redirection Hosts",
      "404 Hosts",
      "Custom Pages",
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

describe("dashboard route", () => {
  it("puts the dashboard first, before Proxy Hosts", () => {
    expect(primaryNav[0].href).toBe("/");
    expect(primaryNav[1].href).toBe("/proxy-hosts");
  });

  it("marks the dashboard active only on itself", () => {
    // "/" is a prefix of every path, so a naive startsWith would light the
    // dashboard up on every page in the app.
    expect(isActivePath("/", "/")).toBe(true);
    expect(isActivePath("/proxy-hosts", "/")).toBe(false);
    expect(isActivePath("/certificates", "/")).toBe(false);
  });

  it("still matches nested routes for other entries", () => {
    expect(isActivePath("/proxy-hosts/3", "/proxy-hosts")).toBe(true);
    expect(isActivePath("/proxy-hosts-other", "/proxy-hosts")).toBe(false);
  });
});
