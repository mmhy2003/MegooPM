import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { accessLists, type AccessList } from "@/lib/api";
import { AccessListDialog } from "@/components/access-lists/access-list-dialog";

const STAMPS = {
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

function makeList(overrides: Partial<AccessList> = {}): AccessList {
  return {
    id: 3,
    name: "ops",
    satisfy_any: true,
    pass_auth: false,
    auth_users: [
      { id: 11, username: "alice", ...STAMPS },
      { id: 12, username: "bob", ...STAMPS },
    ],
    client_rules: [{ id: 21, address: "10.0.0.0/8", directive: "allow", ...STAMPS }],
    ...STAMPS,
    ...overrides,
  };
}

function renderDialog(list: AccessList | null = null) {
  return render(
    <AccessListDialog open onOpenChange={() => {}} list={list} onSaved={() => {}} />,
  );
}

describe("AccessListDialog", () => {
  beforeEach(() => {
    vi.spyOn(accessLists, "create").mockResolvedValue(makeList());
    vi.spyOn(accessLists, "update").mockResolvedValue(makeList());
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("offers the details, authorization and access tabs", () => {
    renderDialog();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Details",
      "Authorization",
      "Access",
    ]);
  });

  it("creates the list, its users and its rules in a single request", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByLabelText("Name"), "internal");

    await user.click(screen.getByRole("tab", { name: "Authorization" }));
    await user.type(screen.getAllByLabelText("Username")[0], "alice");
    await user.type(screen.getAllByLabelText("Password")[0], "s3cret");

    await user.click(screen.getByRole("tab", { name: "Access" }));
    await user.type(screen.getAllByLabelText("Address")[0], "10.0.0.0/8");

    await user.click(screen.getByRole("button", { name: "Create list" }));

    await waitFor(() => expect(accessLists.create).toHaveBeenCalledTimes(1));
    expect(accessLists.create).toHaveBeenCalledWith({
      name: "internal",
      satisfy_any: false,
      pass_auth: false,
      auth_users: [{ username: "alice", password: "s3cret" }],
      clients: [{ address: "10.0.0.0/8", directive: "allow" }],
    });
  });

  it("seeds every tab from an existing list", async () => {
    const user = userEvent.setup();
    renderDialog(makeList());

    expect(screen.getByLabelText("Name")).toHaveValue("ops");
    expect(screen.getByLabelText("Satisfy Any")).toHaveAttribute("aria-checked", "true");

    await user.click(screen.getByRole("tab", { name: "Authorization" }));
    expect(screen.getAllByLabelText("Username").map((i) => (i as HTMLInputElement).value)).toEqual(
      ["alice", "bob"],
    );
    // Hashes are never returned, so the field starts empty and says so.
    const password = screen.getAllByLabelText("Password")[0];
    expect(password).toHaveValue("");
    expect(password).toHaveAttribute("placeholder", "unchanged");

    await user.click(screen.getByRole("tab", { name: "Access" }));
    expect(screen.getAllByLabelText("Address")[0]).toHaveValue("10.0.0.0/8");
  });

  it("saves the whole form in one request, omitting untouched passwords", async () => {
    const user = userEvent.setup();
    renderDialog(makeList());

    await user.click(screen.getByRole("tab", { name: "Authorization" }));
    await user.type(screen.getAllByLabelText("Password")[1], "bob-new");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(accessLists.update).toHaveBeenCalledTimes(1));
    expect(accessLists.update).toHaveBeenCalledWith(3, {
      name: "ops",
      satisfy_any: true,
      pass_auth: false,
      auth_users: [{ username: "alice" }, { username: "bob", password: "bob-new" }],
      clients: [{ address: "10.0.0.0/8", directive: "allow" }],
    });
  });

  it("drops a removed user from the saved collection", async () => {
    const user = userEvent.setup();
    renderDialog(makeList());

    await user.click(screen.getByRole("tab", { name: "Authorization" }));
    await user.click(screen.getByRole("button", { name: "Remove user 1" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(accessLists.update).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(accessLists.update).mock.calls[0][1];
    expect(payload.auth_users).toEqual([{ username: "bob" }]);
  });

  it("adds further rows on demand", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("tab", { name: "Authorization" }));
    await user.click(screen.getByRole("button", { name: "Add user" }));
    expect(screen.getAllByLabelText("Username")).toHaveLength(2);
  });

  it("reveals the offending tab instead of saving a half-filled row", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByLabelText("Name"), "internal");
    await user.click(screen.getByRole("tab", { name: "Authorization" }));
    await user.type(screen.getAllByLabelText("Username")[0], "dave");

    // Submit from a different tab; the error must pull us back to the problem.
    await user.click(screen.getByRole("tab", { name: "Details" }));
    await user.click(screen.getByRole("button", { name: "Create list" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a password for “dave”.");
    expect(screen.getByRole("tab", { name: "Authorization" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(accessLists.create).not.toHaveBeenCalled();
  });

  it("ignores a row left untouched", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByLabelText("Name"), "bare");
    await user.click(screen.getByRole("button", { name: "Create list" }));

    await waitFor(() => expect(accessLists.create).toHaveBeenCalledTimes(1));
    expect(vi.mocked(accessLists.create).mock.calls[0][0]).toMatchObject({
      auth_users: [],
      clients: [],
    });
  });
});
