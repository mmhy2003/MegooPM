import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { accessLists, type AccessList } from "@/lib/api";
import { AccessListsView } from "@/components/access-lists/access-lists-view";

// vi.mock factories are hoisted above the imports, so a factory closing
// over a plain const fails at collection. vi.hoisted is the way in.
const useAuth = vi.hoisted(() => vi.fn(() => ({ user: { role: "admin" } })));
vi.mock("@/lib/auth/context", () => ({ useAuth }));

const useIsMobile = vi.hoisted(() => vi.fn(() => false));
vi.mock("@/hooks/use-mobile", () => ({ useIsMobile }));

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

describe("AccessListsView on a phone", () => {
  afterEach(() => {
    useIsMobile.mockReturnValue(false);
  });

  it("lays each list out as a card, with its actions", async () => {
    useIsMobile.mockReturnValue(true);
    vi.spyOn(accessLists, "list").mockResolvedValue([makeList()]);
    render(<AccessListsView />);

    expect(await screen.findByText("office")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit office" })).toBeInTheDocument();
  });
});
