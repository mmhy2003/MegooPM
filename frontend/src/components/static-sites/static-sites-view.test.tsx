import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import {
  accessLists,
  certificates,
  staticSites,
  type StaticSite,
  type StaticSiteFolders,
} from "@/lib/api";
import { StaticSitesView } from "@/components/static-sites/static-sites-view";

const useAuth = vi.hoisted(() => vi.fn(() => ({ user: { role: "admin" } })));
vi.mock("@/lib/auth/context", () => ({ useAuth }));

const useIsMobile = vi.hoisted(() => vi.fn(() => false));
vi.mock("@/hooks/use-mobile", () => ({ useIsMobile }));

function makeSite(over: Partial<StaticSite> = {}): StaticSite {
  return {
    id: 1,
    domain_names: ["www.example.com"],
    folder: "company",
    folder_status: "ok",
    access_list_id: null,
    certificate_id: null,
    enabled: true,
    ssl_forced: false,
    http2_support: false,
    hsts_enabled: false,
    hsts_subdomains: false,
    block_exploits: false,
    crowdsec_enabled: true,
    advanced_config: "",
    created_at: "2026-11-25T00:00:00Z",
    updated_at: "2026-11-25T00:00:00Z",
    ...over,
  };
}

const MOUNTED: StaticSiteFolders = {
  available: true,
  folders: [{ path: "company", has_index: true, readable: true }],
  reason: null,
};

async function renderView(rows: StaticSite[], folders: StaticSiteFolders = MOUNTED) {
  vi.spyOn(staticSites, "list").mockResolvedValue(rows);
  vi.spyOn(staticSites, "folders").mockResolvedValue(folders);
  vi.spyOn(certificates, "list").mockResolvedValue([]);
  vi.spyOn(accessLists, "list").mockResolvedValue([]);
  render(<StaticSitesView />);
  await screen.findByRole("searchbox", { name: "Search static sites" });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useIsMobile.mockReturnValue(false);
});

describe("StaticSitesView", () => {
  it("lists each site's domain and folder", async () => {
    await renderView([makeSite()]);
    expect(await screen.findByText("www.example.com")).toBeInTheDocument();
    expect(screen.getByText("company")).toBeInTheDocument();
  });

  it("warns when a site's folder is missing", async () => {
    await renderView([makeSite({ folder: "gone", folder_status: "missing" })]);
    expect(await screen.findByText("Folder not found")).toBeInTheDocument();
  });

  it("names every folder problem in words", async () => {
    await renderView([
      makeSite({ id: 1, domain_names: ["a.test"], folder_status: "no_index" }),
      makeSite({ id: 2, domain_names: ["b.test"], folder_status: "unreadable" }),
      makeSite({ id: 3, domain_names: ["c.test"], folder_status: "symlink" }),
    ]);
    expect(await screen.findByText("No index.html")).toBeInTheDocument();
    expect(screen.getByText("Can't read folder")).toBeInTheDocument();
    expect(screen.getByText("Symlinks aren't served")).toBeInTheDocument();
  });

  it("explains how to mount the sites folder when there is none", async () => {
    await renderView([], { available: false, folders: [], reason: "not_mounted" });
    expect(await screen.findByText(/No sites folder mounted/)).toBeInTheDocument();
    expect(screen.getByText(/SITES_PATH/)).toBeInTheDocument();
  });

  it("shows the folder warning on phones too", async () => {
    useIsMobile.mockReturnValue(true);
    await renderView([makeSite({ folder_status: "missing" })]);
    expect(await screen.findByText("Folder not found")).toBeInTheDocument();
  });
});
