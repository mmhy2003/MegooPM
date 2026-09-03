import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { certificates, redirectionHosts, type RedirectionHost } from "@/lib/api";
import { RedirectionHostsView } from "@/components/redirection-hosts/redirection-hosts-view";

function makeHost(over: Partial<RedirectionHost> = {}): RedirectionHost {
  return {
    id: 1,
    domain_names: ["old.example.com"],
    forward_domain_name: "new.example.com",
    forward_scheme: "auto",
    forward_http_code: 301,
    preserve_path: true,
    certificate_id: null,
    enabled: true,
    ssl_forced: false,
    http2_support: false,
    hsts_enabled: false,
    hsts_subdomains: false,
    block_exploits: false,
    advanced_config: "",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    ...over,
  } as RedirectionHost;
}

async function renderView(rows: RedirectionHost[]) {
  vi.spyOn(redirectionHosts, "list").mockResolvedValue(rows);
  vi.spyOn(certificates, "list").mockResolvedValue([]);
  render(<RedirectionHostsView />);
  await screen.findByRole("searchbox", { name: "Search redirection hosts" });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("RedirectionHostsView search", () => {
  it("matches the redirect target as well as the source domain", async () => {
    const user = userEvent.setup();
    await renderView([
      makeHost({
        id: 1,
        domain_names: ["old.example.com"],
        forward_domain_name: "new.example.com",
      }),
      makeHost({
        id: 2,
        domain_names: ["legacy.internal"],
        forward_domain_name: "current.internal",
      }),
    ]);

    await user.type(screen.getByRole("searchbox"), "current.internal");

    expect(screen.getByText("legacy.internal")).toBeInTheDocument();
    expect(screen.queryByText("old.example.com")).not.toBeInTheDocument();
  });

  it("distinguishes a filtered-empty table from an empty instance", async () => {
    const user = userEvent.setup();
    await renderView([]);
    expect(screen.getByText(/no redirection hosts yet/i)).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox"), "nonesuch");

    expect(screen.getByText(/no redirection hosts match/i)).toBeInTheDocument();
  });
});
