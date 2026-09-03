import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { accessLists, type AccessList } from "@/lib/api";
import { AccessListsView } from "@/components/access-lists/access-lists-view";

function makeList(over: Partial<AccessList> = {}): AccessList {
  return {
    id: 1,
    name: "office",
    satisfy_any: false,
    pass_auth: false,
    auth_users: [],
    client_rules: [],
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    ...over,
  } as AccessList;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("AccessListsView search", () => {
  it("narrows the table by name", async () => {
    const user = userEvent.setup();
    vi.spyOn(accessLists, "list").mockResolvedValue([
      makeList({ id: 1, name: "office" }),
      makeList({ id: 2, name: "partners" }),
    ]);
    render(<AccessListsView />);
    await screen.findByRole("searchbox", { name: "Search access lists" });

    await user.type(screen.getByRole("searchbox"), "partner");

    expect(screen.getByText("partners")).toBeInTheDocument();
    expect(screen.queryByText("office")).not.toBeInTheDocument();
  });

  it("distinguishes a filtered-empty table from an empty instance", async () => {
    const user = userEvent.setup();
    vi.spyOn(accessLists, "list").mockResolvedValue([]);
    render(<AccessListsView />);
    await screen.findByRole("searchbox", { name: "Search access lists" });
    expect(screen.getByText(/no access lists yet/i)).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox"), "nonesuch");

    expect(screen.getByText(/no access lists match/i)).toBeInTheDocument();
  });
});
