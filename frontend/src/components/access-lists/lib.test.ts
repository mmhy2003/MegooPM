import { describe, expect, it } from "vitest";

import {
  blankClientRow,
  blankUserRow,
  buildCreatePayload,
  buildUpdatePayload,
  emptyFormState,
  normalizeAddress,
  satisfyDescription,
  satisfyLabel,
  stateFromList,
  validateAccessListForm,
  type AccessListFormState,
} from "@/components/access-lists/lib";
import type { AccessList } from "@/lib/api";

const LIST: AccessList = {
  id: 3,
  name: "ops",
  satisfy_any: true,
  pass_auth: false,
  auth_users: [
    {
      id: 11,
      username: "alice",
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
    },
  ],
  client_rules: [
    {
      id: 21,
      address: "10.0.0.0/8",
      directive: "allow",
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
    },
  ],
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

function state(overrides: Partial<AccessListFormState> = {}): AccessListFormState {
  return { ...emptyFormState(), name: "ok", users: [], clients: [], ...overrides };
}

describe("satisfyLabel", () => {
  it("maps satisfy_any to the gate word", () => {
    expect(satisfyLabel(true)).toBe("Any");
    expect(satisfyLabel(false)).toBe("All");
  });
});

describe("satisfyDescription", () => {
  it("describes an OR gate when satisfy_any", () => {
    expect(satisfyDescription(true)).toMatch(/EITHER/);
  });
  it("describes an AND gate otherwise", () => {
    expect(satisfyDescription(false)).toMatch(/BOTH/);
  });
});

describe("normalizeAddress", () => {
  it("trims surrounding whitespace", () => {
    expect(normalizeAddress("  10.0.0.1 ")).toBe("10.0.0.1");
  });
  it("lower-cases the 'all' keyword", () => {
    expect(normalizeAddress("ALL")).toBe("all");
    expect(normalizeAddress(" All ")).toBe("all");
  });
  it("preserves other addresses verbatim (backend validates)", () => {
    expect(normalizeAddress("192.168.0.0/16")).toBe("192.168.0.0/16");
    expect(normalizeAddress("2001:DB8::/32")).toBe("2001:DB8::/32");
  });
});

describe("emptyFormState", () => {
  it("offers one blank row of each kind so the options are visible", () => {
    const fresh = emptyFormState();
    expect(fresh.name).toBe("");
    expect(fresh.users).toHaveLength(1);
    expect(fresh.clients).toHaveLength(1);
    expect(fresh.users[0].id).toBeUndefined();
  });
});

describe("stateFromList", () => {
  it("seeds every field from the server list without inventing blank rows", () => {
    const seeded = stateFromList(LIST);
    expect(seeded.name).toBe("ops");
    expect(seeded.satisfyAny).toBe(true);
    expect(seeded.passAuth).toBe(false);
    expect(seeded.users).toEqual([{ id: 11, username: "alice", password: "" }]);
    expect(seeded.clients).toEqual([{ id: 21, address: "10.0.0.0/8", directive: "allow" }]);
  });
});

describe("validateAccessListForm", () => {
  it("requires a name and points at the details tab", () => {
    expect(validateAccessListForm(state({ name: "  " }))).toEqual({
      tab: "details",
      message: "Enter a name for the access list.",
    });
  });

  it("accepts an existing user whose password is left blank", () => {
    const form = state({ users: [{ id: 11, username: "alice", password: "" }] });
    expect(validateAccessListForm(form)).toBeNull();
  });

  it("rejects a new user with no password and points at the authorization tab", () => {
    const form = state({ users: [{ ...blankUserRow(), username: "dave" }] });
    expect(validateAccessListForm(form)).toEqual({
      tab: "authorization",
      message: "Enter a password for “dave”.",
    });
  });

  it("rejects a password typed with no username", () => {
    const form = state({ users: [{ ...blankUserRow(), password: "hunter2" }] });
    expect(validateAccessListForm(form)?.tab).toBe("authorization");
  });

  it("rejects duplicate usernames", () => {
    const form = state({
      users: [
        { ...blankUserRow(), username: "alice", password: "a" },
        { ...blankUserRow(), username: "alice", password: "b" },
      ],
    });
    expect(validateAccessListForm(form)).toEqual({
      tab: "authorization",
      message: "“alice” is listed twice — usernames must be unique.",
    });
  });

  it("rejects a rule with no address and points at the access tab", () => {
    const form = state({ clients: [{ ...blankClientRow(), directive: "deny" }] });
    expect(validateAccessListForm(form)).toEqual({
      tab: "access",
      message: "Enter an IP, CIDR, or “all” for every rule.",
    });
  });

  it("ignores rows left completely blank", () => {
    const form = state({ users: [blankUserRow()], clients: [blankClientRow()] });
    expect(validateAccessListForm(form)).toBeNull();
  });
});

describe("buildCreatePayload", () => {
  it("drops blank rows and normalizes addresses", () => {
    const form = state({
      name: "  ops  ",
      satisfyAny: true,
      users: [{ ...blankUserRow(), username: " alice ", password: "pw" }, blankUserRow()],
      clients: [
        { ...blankClientRow(), address: " ALL ", directive: "deny" },
        blankClientRow(),
      ],
    });
    expect(buildCreatePayload(form)).toEqual({
      name: "ops",
      satisfy_any: true,
      pass_auth: false,
      auth_users: [{ username: "alice", password: "pw" }],
      clients: [{ address: "all", directive: "deny" }],
    });
  });
});

describe("buildUpdatePayload", () => {
  it("omits the password of an untouched existing user", () => {
    const form = state({
      users: [
        { id: 11, username: "alice", password: "" },
        { id: 12, username: "bob", password: "new-pw" },
      ],
    });
    expect(buildUpdatePayload(form).auth_users).toEqual([
      { username: "alice" },
      { username: "bob", password: "new-pw" },
    ]);
  });

  it("always sends both collections so removals are applied", () => {
    const payload = buildUpdatePayload(state({ users: [], clients: [] }));
    expect(payload.auth_users).toEqual([]);
    expect(payload.clients).toEqual([]);
  });
});
