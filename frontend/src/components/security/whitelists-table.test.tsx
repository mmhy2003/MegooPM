import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { WhitelistsTable } from "@/components/security/whitelists-table";
import type { Whitelist } from "@/lib/api";

const ROW: Whitelist = {
  id: 1,
  name: "Internal Backends",
  reason: "internal backends trip appsec generic rules",
  description: "",
  ips: ["10.10.0.14"],
  cidrs: ["10.10.0.0/24"],
  enabled: true,
  created_at: "2026-08-31T00:00:00Z",
  updated_at: "2026-08-31T00:00:00Z",
};

afterEach(cleanup);

describe("WhitelistsTable", () => {
  it("shows the name, reason and how many addresses it covers", () => {
    render(
      <WhitelistsTable rows={[ROW]} onToggle={async () => {}} onEdit={() => {}} onDelete={() => {}} />,
    );
    expect(screen.getByText("Internal Backends")).toBeInTheDocument();
    expect(screen.getByText(/1 IP, 1 CIDR/)).toBeInTheDocument();
  });

  it("pluralises the coverage summary", () => {
    render(
      <WhitelistsTable
        rows={[{ ...ROW, ips: ["10.0.0.1", "10.0.0.2"], cidrs: [] }]}
        onToggle={async () => {}}
        onEdit={() => {}}
        onDelete={() => {}}
      />,
    );
    expect(screen.getByText("2 IPs")).toBeInTheDocument();
  });

  it("toggles a whitelist off", async () => {
    const onToggle = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(
      <WhitelistsTable rows={[ROW]} onToggle={onToggle} onEdit={() => {}} onDelete={() => {}} />,
    );
    await user.click(screen.getByRole("switch"));
    expect(onToggle).toHaveBeenCalledWith(ROW, false);
  });

  it("edits and deletes by row", async () => {
    const onEdit = vi.fn();
    const onDelete = vi.fn();
    const user = userEvent.setup();
    render(
      <WhitelistsTable rows={[ROW]} onToggle={async () => {}} onEdit={onEdit} onDelete={onDelete} />,
    );
    await user.click(screen.getByRole("button", { name: "Edit Internal Backends" }));
    await user.click(screen.getByRole("button", { name: "Delete Internal Backends" }));
    expect(onEdit).toHaveBeenCalledWith(ROW);
    expect(onDelete).toHaveBeenCalledWith(ROW);
  });

  it("tells the operator what an empty list means", () => {
    render(
      <WhitelistsTable rows={[]} onToggle={async () => {}} onEdit={() => {}} onDelete={() => {}} />,
    );
    expect(screen.getByText(/No whitelists yet/i)).toBeInTheDocument();
  });
});
