import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { staticSites, type StaticSite, type StaticSiteFolders } from "@/lib/api";
import { StaticSiteDialog } from "@/components/static-sites/static-site-dialog";

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }));
vi.mock("sonner", () => ({ toast }));

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

const FOLDERS: StaticSiteFolders = {
  available: true,
  folders: [
    { path: "company", has_index: true, readable: true },
    { path: "drafts", has_index: false, readable: true },
  ],
  reason: null,
};

function renderDialog(site: StaticSite | null = null) {
  return render(
    <StaticSiteDialog
      open
      onOpenChange={() => {}}
      site={site}
      certificates={[]}
      lists={[]}
      folders={FOLDERS}
      onSaved={() => {}}
    />,
  );
}

async function addDomain(user: ReturnType<typeof userEvent.setup>, domain: string) {
  await user.type(screen.getByLabelText("Domain names"), `${domain}{Enter}`);
}

describe("StaticSiteDialog", () => {
  beforeEach(() => {
    vi.spyOn(staticSites, "create").mockResolvedValue(makeSite());
    vi.spyOn(staticSites, "update").mockResolvedValue(makeSite());
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    toast.success.mockReset();
    toast.error.mockReset();
    toast.warning.mockReset();
  });

  it("splits the form into Details, SSL and Advanced tabs", () => {
    renderDialog();
    expect(screen.getByLabelText("Domain names")).toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Details",
      "SSL",
      "Advanced",
    ]);
  });

  it("creates a site with the folder picked from the list", async () => {
    const user = userEvent.setup();
    renderDialog();
    await addDomain(user, "www.example.com");
    await user.click(screen.getByRole("combobox", { name: "Folder" }));
    await user.click(await screen.findByRole("option", { name: "company" }));
    await user.click(screen.getByRole("button", { name: "Create site" }));

    await waitFor(() => expect(staticSites.create).toHaveBeenCalledTimes(1));
    expect(vi.mocked(staticSites.create).mock.calls[0][0]).toMatchObject({
      domain_names: ["www.example.com"],
      folder: "company",
    });
  });

  it("marks folders that have no index.html", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("combobox", { name: "Folder" }));
    expect(await screen.findByRole("option", { name: "drafts · no index.html" })).toBeInTheDocument();
  });

  it("refuses a typed folder that climbs out of the sites folder", async () => {
    const user = userEvent.setup();
    renderDialog();
    await addDomain(user, "www.example.com");
    await user.click(screen.getByRole("button", { name: "Type a folder path" }));
    await user.type(screen.getByLabelText("Folder"), "../x");
    await user.click(screen.getByRole("button", { name: "Create site" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/folder/i);
    expect(staticSites.create).not.toHaveBeenCalled();
  });

  it("accepts a typed nested folder", async () => {
    const user = userEvent.setup();
    renderDialog();
    await addDomain(user, "www.example.com");
    await user.click(screen.getByRole("button", { name: "Type a folder path" }));
    await user.type(screen.getByLabelText("Folder"), "clients/acme");
    await user.click(screen.getByRole("button", { name: "Create site" }));

    await waitFor(() => expect(staticSites.create).toHaveBeenCalledTimes(1));
    expect(vi.mocked(staticSites.create).mock.calls[0][0]).toMatchObject({
      folder: "clients/acme",
    });
  });

  it("asks for a folder before saving", async () => {
    const user = userEvent.setup();
    renderDialog();
    await addDomain(user, "www.example.com");
    await user.click(screen.getByRole("button", { name: "Create site" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a folder.");
    expect(staticSites.create).not.toHaveBeenCalled();
  });

  it("saves, but warns, when the folder has no index.html yet", async () => {
    vi.mocked(staticSites.update).mockResolvedValue(makeSite({ folder_status: "no_index" }));
    const user = userEvent.setup();
    renderDialog(makeSite());
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(toast.warning).toHaveBeenCalledTimes(1));
    expect(toast.warning.mock.calls[0][0]).toMatch(/No index\.html/);
  });

  it("keeps a saved folder that the list no longer shows", () => {
    renderDialog(makeSite({ folder: "gone", folder_status: "missing" }));
    expect(screen.getByRole("combobox", { name: "Folder" })).toHaveTextContent("gone");
  });
});
