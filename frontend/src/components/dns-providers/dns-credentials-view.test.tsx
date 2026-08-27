import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { dnsCredentials, dnsProviders } from "@/lib/api";
import { DnsCredentialsView } from "@/components/dns-providers/dns-credentials-view";

const catalog = [
  {
    id: "cloudflare",
    label: "Cloudflare",
    description: "Token or global key",
    fields: [
      { name: "auth_token", label: "Auth token", help: "API token", secret: true },
      { name: "zone_id", label: "Zone id", help: "optional", secret: false },
    ],
  },
];
const cred = {
  id: 1,
  name: "cf-prod",
  provider: "cloudflare",
  provider_label: "Cloudflare",
  options: { zone_id: "z1" },
  secret_fields: ["auth_token"],
  in_use_by: [{ id: 9, name: "wildcard" }],
  created_at: "2026-08-27T09:00:00Z",
  updated_at: "2026-08-27T09:00:00Z",
};

vi.mock("@/components/dns-providers/dns-credential-dialog", () => ({
  DnsCredentialDialog: ({ open, onSaved }: { open: boolean; onSaved: () => void }) =>
    open ? (
      <button type="button" onClick={onSaved}>
        confirm-save
      </button>
    ) : null,
}));
vi.mock("@/components/dns-providers/verify-credential-dialog", () => ({
  VerifyCredentialDialog: ({ open }: { open: boolean }) =>
    open ? <div>verify-dialog</div> : null,
}));
vi.mock("@/components/proxy-hosts/confirm-delete-dialog", () => ({
  ConfirmDeleteDialog: ({
    open,
    onConfirm,
    onDeleted,
  }: {
    open: boolean;
    onConfirm: () => Promise<void>;
    onDeleted: () => void;
  }) =>
    open ? (
      <button type="button" onClick={() => void onConfirm().then(onDeleted)}>
        confirm-delete
      </button>
    ) : null,
}));

describe("DnsCredentialsView", () => {
  beforeEach(() => {
    vi.spyOn(dnsProviders, "catalog").mockResolvedValue(catalog);
    vi.spyOn(dnsCredentials, "list").mockResolvedValue([cred]);
    vi.spyOn(dnsCredentials, "remove").mockResolvedValue(undefined);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("lists credentials with provider label, secret names and usage", async () => {
    render(<DnsCredentialsView />);
    const row = (await screen.findByText("cf-prod")).closest("tr") as HTMLElement;
    expect(within(row).getByText("Cloudflare")).toBeInTheDocument();
    expect(within(row).getByText("auth_token")).toBeInTheDocument();
    expect(within(row).getByText("1")).toHaveAttribute("title", "wildcard");
  });

  it("opens the verify dialog", async () => {
    const user = userEvent.setup();
    render(<DnsCredentialsView />);
    const row = (await screen.findByText("cf-prod")).closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Verify cf-prod" }));
    expect(screen.getByText("verify-dialog")).toBeInTheDocument();
  });

  it("deletes and refetches", async () => {
    const user = userEvent.setup();
    render(<DnsCredentialsView />);
    const row = (await screen.findByText("cf-prod")).closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Delete cf-prod" }));
    await user.click(screen.getByRole("button", { name: "confirm-delete" }));
    await waitFor(() => expect(dnsCredentials.remove).toHaveBeenCalledWith(1));
    await waitFor(() => expect(dnsCredentials.list).toHaveBeenCalledTimes(2));
  });

  it("refetches after the dialog saves", async () => {
    const user = userEvent.setup();
    render(<DnsCredentialsView />);
    await screen.findByText("cf-prod");
    await user.click(screen.getByRole("button", { name: /new credentials/i }));
    await user.click(screen.getByRole("button", { name: "confirm-save" }));
    await waitFor(() => expect(dnsCredentials.list).toHaveBeenCalledTimes(2));
  });

  it("shows the load error state", async () => {
    vi.spyOn(dnsCredentials, "list").mockRejectedValueOnce(new Error("boom"));
    render(<DnsCredentialsView />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });
});
