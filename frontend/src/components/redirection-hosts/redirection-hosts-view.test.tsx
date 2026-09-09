import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { certificates, redirectionHosts, type RedirectionHost } from "@/lib/api";
import { RedirectionHostsView } from "@/components/redirection-hosts/redirection-hosts-view";

// vi.mock factories are hoisted above the imports, so a factory closing
// over a plain const fails at collection. vi.hoisted is the way in.
const useAuth = vi.hoisted(() => vi.fn(() => ({ user: { role: "admin" } })));
vi.mock("@/lib/auth/context", () => ({ useAuth }));

const useIsMobile = vi.hoisted(() => vi.fn(() => false));
vi.mock("@/hooks/use-mobile", () => ({ useIsMobile }));

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

describe("RedirectionHostsView target link", () => {
  it("links the target so an operator can open it", async () => {
    await renderView([
      makeHost({ forward_scheme: "https", forward_domain_name: "new.example.com" }),
    ]);

    const link = screen.getByRole("link", { name: "https://new.example.com" });
    expect(link).toHaveAttribute("href", "https://new.example.com");
    expect(link).toHaveAttribute("target", "_blank");
    // Without noopener the opened page can navigate this one back.
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("links an http target over http", async () => {
    await renderView([
      makeHost({ forward_scheme: "http", forward_domain_name: "plain.example.com" }),
    ]);

    expect(screen.getByRole("link", { name: "http://plain.example.com" })).toHaveAttribute(
      "href",
      "http://plain.example.com",
    );
  });

  it("opens an auto target over https, keeping the cell text unprefixed", async () => {
    // 'auto' means "whatever scheme the visitor arrived on", which a link from
    // here does not have. The cell still shows no prefix, as before.
    await renderView([makeHost({ forward_scheme: "auto" })]);

    const link = screen.getByRole("link", { name: "new.example.com" });
    expect(link).toHaveAttribute("href", "https://new.example.com");
  });

  it("leaves a wildcard target as plain text", async () => {
    // No single address sits behind it, so there is nothing to open.
    await renderView([makeHost({ forward_domain_name: "*.example.com" })]);

    // Narrow to the target: the source domain beside it is a link either way.
    expect(screen.queryByRole("link", { name: "*.example.com" })).not.toBeInTheDocument();
    expect(screen.getByText("*.example.com")).toBeInTheDocument();
  });
});

describe("RedirectionHostsView on a phone", () => {
  afterEach(() => {
    useIsMobile.mockReturnValue(false);
  });

  it("lays each host out as a card, with its actions", async () => {
    useIsMobile.mockReturnValue(true);
    await renderView([makeHost()]);

    expect(screen.getByText("old.example.com")).toBeInTheDocument();
    expect(screen.getByText(/new\.example\.com/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Enable old.example.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit old.example.com" })).toBeInTheDocument();
  });
});

describe("RedirectionHostsView id column", () => {
  it("shows each host's database id", async () => {
    await renderView([makeHost({ id: 42 })]);

    expect(screen.getByText("42")).toBeInTheDocument();
  });
});
