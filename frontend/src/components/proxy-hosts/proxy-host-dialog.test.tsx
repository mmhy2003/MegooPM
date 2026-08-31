import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { proxyHosts, type Upstream } from "@/lib/api";
import { ProxyHostDialog } from "@/components/proxy-hosts/proxy-host-dialog";
import { makeHost } from "@/components/proxy-hosts/test-utils";

const pools: Upstream[] = [
  {
    id: 1,
    name: "app-pool",
    description: "",
    lb_method: "round_robin",
    context: "http",
    enabled: true,
    backends: [],
    created_at: "2026-08-27T00:00:00Z",
    updated_at: "2026-08-27T00:00:00Z",
  },
  {
    id: 2,
    name: "api-pool",
    description: "",
    lb_method: "round_robin",
    context: "http",
    enabled: true,
    backends: [],
    created_at: "2026-08-27T00:00:00Z",
    updated_at: "2026-08-27T00:00:00Z",
  },
];

function renderDialog(host = makeHost()) {
  return render(
    <ProxyHostDialog
      open
      onOpenChange={() => {}}
      host={host}
      pools={pools}
      lists={[]}
      certs={[]}
      onSaved={() => {}}
    />,
  );
}

describe("ProxyHostDialog", () => {
  beforeEach(() => {
    vi.spyOn(proxyHosts, "update").mockResolvedValue(makeHost());
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("keeps domains/access list/enabled outside three tabs", () => {
    renderDialog();
    expect(screen.getByLabelText("Domain names")).toBeInTheDocument();
    expect(screen.getByLabelText("Access list")).toBeInTheDocument();
    expect(screen.getByLabelText("Enabled")).toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Forwarding",
      "Certificate",
      "Advanced",
    ]);
    expect(screen.getByRole("button", { name: "Add location" })).toBeInTheDocument();
  });

  it("disables the TLS toggles while no certificate is selected", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("tab", { name: "Certificate" }));
    expect(await screen.findByRole("combobox", { name: "Certificate" })).toBeInTheDocument();
    // base-ui renders <span role="switch" aria-disabled>, not a disabled attribute.
    for (const name of ["Force SSL", "HTTP/2", "HSTS", "HSTS subdomains"]) {
      expect(screen.getByLabelText(name)).toHaveAttribute("aria-disabled", "true");
    }
  });

  it("enables the TLS toggles when the host has a certificate", async () => {
    const user = userEvent.setup();
    renderDialog(makeHost({ certificate_id: 7 }));
    await user.click(screen.getByRole("tab", { name: "Certificate" }));
    expect(await screen.findByLabelText("Force SSL")).not.toHaveAttribute("aria-disabled", "true");
  });

  it("jumps to the Forwarding tab and reports a bad location on save", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: "Add location" }));
    await user.type(screen.getByLabelText("Location path"), "api");
    await user.click(screen.getByRole("tab", { name: "Advanced" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      'Location path "api" must start with /.',
    );
    expect(screen.getByRole("tab", { name: "Forwarding" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(proxyHosts.update).not.toHaveBeenCalled();
  });

  it("saves locations and the certificate in the payload", async () => {
    const user = userEvent.setup();
    renderDialog(
      makeHost({
        certificate_id: 7,
        locations: [{ id: 5, path: "/api/", upstream_id: 2, forward_scheme: "https" }],
      }),
    );
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(proxyHosts.update).toHaveBeenCalledTimes(1));
    expect(vi.mocked(proxyHosts.update).mock.calls[0][1]).toMatchObject({
      upstream_id: 1,
      certificate_id: 7,
      locations: [{ path: "/api/", upstream_id: 2, forward_scheme: "https" }],
    });
  });

  it("exposes CrowdSec protection on the Advanced tab and saves it", async () => {
    const user = userEvent.setup();
    renderDialog(makeHost({ crowdsec_enabled: false }));
    await user.click(screen.getByRole("tab", { name: "Advanced" }));
    const toggle = await screen.findByLabelText("CrowdSec protection");
    expect(toggle).toHaveAttribute("aria-checked", "false");
    await user.click(toggle);
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(proxyHosts.update).toHaveBeenCalledTimes(1));
    expect(vi.mocked(proxyHosts.update).mock.calls[0][1]).toMatchObject({
      crowdsec_enabled: true,
    });
  });
});

describe("ProxyHostDialog forward target", () => {
  beforeEach(() => {
    vi.spyOn(proxyHosts, "update").mockResolvedValue(makeHost());
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows the root route's kind and target", async () => {
    renderDialog();
    expect(await screen.findByLabelText("Root target kind")).toBeInTheDocument();
    expect(screen.getByLabelText("Upstream pool")).toBeInTheDocument();
  });

  it("swaps the root cell to host and port", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByLabelText("Root target kind"));
    await user.click(await screen.findByRole("option", { name: "Single host" }));

    expect(screen.getByLabelText("Root forward host")).toBeInTheDocument();
    expect(screen.queryByLabelText("Upstream pool")).not.toBeInTheDocument();
  });

  it("switches one location row without touching its siblings", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: "Add location" }));
    await user.click(screen.getByRole("button", { name: "Add location" }));

    // Root + two locations = three pool selects before, two after.
    expect(screen.getAllByLabelText("Upstream pool")).toHaveLength(3);
    const kinds = screen.getAllByLabelText("Location target kind");
    await user.click(kinds[0]);
    await user.click(await screen.findByRole("option", { name: "Single host" }));

    expect(screen.getAllByLabelText("Upstream pool")).toHaveLength(2);
    expect(screen.getAllByLabelText("Location forward host")).toHaveLength(1);
  });
});

describe("ProxyHostDialog select labels", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows the certificate's name, not its id", async () => {
    const user = userEvent.setup();
    render(
      <ProxyHostDialog
        open
        onOpenChange={() => {}}
        host={makeHost({ certificate_id: 7 })}
        pools={pools}
        lists={[]}
        certs={[{ id: 7, name: "wildcard-cert", status: "active" } as never]}
        onSaved={() => {}}
      />,
    );
    await user.click(screen.getByRole("tab", { name: "Certificate" }));
    // The tab is also called "Certificate", so scope to the control itself.
    expect(await screen.findByRole("combobox", { name: "Certificate" })).toHaveTextContent(
      "wildcard-cert",
    );
  });

  it("shows the access list's name, not its id", () => {
    render(
      <ProxyHostDialog
        open
        onOpenChange={() => {}}
        host={makeHost({ access_list_id: 3 })}
        pools={pools}
        lists={[{ id: 3, name: "office-ips" } as never]}
        certs={[]}
        onSaved={() => {}}
      />,
    );
    expect(screen.getByRole("combobox", { name: "Access list" })).toHaveTextContent("office-ips");
  });
});
