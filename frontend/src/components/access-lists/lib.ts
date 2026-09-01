/**
 * Pure helpers shared by the Access Lists UI.
 *
 * Kept free of React so the form state machine stays unit-testable. The dialog
 * edits a flat {@link AccessListFormState} and saves it in one request:
 * {@link buildCreatePayload} for a new list, {@link buildUpdatePayload} for an
 * existing one, where both collections are sent as whole-collection
 * replacements so removals apply.
 *
 * Error surfacing is delegated to the shared {@link describeError} so the
 * FastAPI error shapes (409 duplicate username, 422 invalid IP/CIDR) render the
 * same way they do across the rest of the app.
 */
import type {
  AccessList,
  AccessListCreate,
  AccessListDirective,
  AccessListUpdate,
} from "@/lib/api";

export { describeError } from "@/components/proxy-hosts/lib";

/** The word shown for `satisfy_any` — "Any" gate vs. "All" gates required. */
export function satisfyLabel(satisfyAny: boolean): "Any" | "All" {
  return satisfyAny ? "Any" : "All";
}

/**
 * One-line summary of how the gates combine, for tooltips / helper text.
 */
export function satisfyDescription(satisfyAny: boolean): string {
  return satisfyAny
    ? "A request passes if it satisfies EITHER basic-auth OR an allow rule."
    : "A request must satisfy BOTH basic-auth AND the IP rules.";
}

/**
 * Trim a client-rule address for submission. `all` is normalized to lower-case;
 * everything else is left as-typed so IPv6 zone ids etc. survive — the backend
 * is the authority on validity and returns 422 for anything malformed.
 */
export function normalizeAddress(input: string): string {
  const trimmed = input.trim();
  return trimmed.toLowerCase() === "all" ? "all" : trimmed;
}

/* -------------------------------------------------------------------------- */
/* Form state                                                                  */
/* -------------------------------------------------------------------------- */

/** The dialog's tabs, in order. Validation reports which one holds the problem. */
export type AccessListTab = "details" | "authorization" | "access";

/**
 * A basic-auth row. `id` marks a user that already exists server-side, which is
 * what makes a blank `password` meaningful: "keep the stored hash". A row with
 * no `id` is new, so it must carry a password.
 */
export type AuthUserRow = {
  id?: number;
  username: string;
  password: string;
};

/** An allow/deny row. `id` marks a rule that already exists server-side. */
export type ClientRow = {
  id?: number;
  address: string;
  directive: AccessListDirective;
};

export type AccessListFormState = {
  name: string;
  satisfyAny: boolean;
  passAuth: boolean;
  users: AuthUserRow[];
  clients: ClientRow[];
};

export function blankUserRow(): AuthUserRow {
  return { username: "", password: "" };
}

export function blankClientRow(): ClientRow {
  return { address: "", directive: "allow" };
}

/**
 * A new list, with one blank row of each kind so both gates are visibly on
 * offer rather than hidden behind an "add" button.
 */
export function emptyFormState(): AccessListFormState {
  return {
    name: "",
    satisfyAny: false,
    passAuth: false,
    users: [blankUserRow()],
    clients: [blankClientRow()],
  };
}

/** Seed the form from a server list, or start empty when there isn't one. */
export function stateFromList(list?: AccessList | null): AccessListFormState {
  if (!list) return emptyFormState();
  return {
    name: list.name,
    satisfyAny: list.satisfy_any ?? false,
    passAuth: list.pass_auth ?? false,
    users: (list.auth_users ?? []).map((u) => ({
      id: u.id,
      username: u.username,
      // Never prefilled: the API does not return credential material. Typing
      // here resets the password; leaving it blank keeps the stored one.
      password: "",
    })),
    clients: (list.client_rules ?? []).map((c) => ({
      id: c.id,
      address: c.address,
      directive: c.directive,
    })),
  };
}

/**
 * A row the user started and abandoned. Only ever true for rows with no `id`:
 * an existing user with an empty password is "unchanged", not blank.
 */
function isBlankUser(row: AuthUserRow): boolean {
  return row.id === undefined && !row.username.trim() && !row.password;
}

/**
 * A rule row is only blank while it is still exactly as it was handed out.
 * Switching the directive to "deny" counts as engaging with the row, so it then
 * demands an address rather than vanishing on save — silently dropping a deny
 * rule the user believed they had added is the worst outcome here.
 */
function isBlankClient(row: ClientRow): boolean {
  return row.id === undefined && !row.address.trim() && row.directive === "allow";
}

/** The rows that will actually be submitted — blank ones are dropped. */
export function submittableUsers(state: AccessListFormState): AuthUserRow[] {
  return state.users.filter((u) => !isBlankUser(u));
}

export function submittableClients(state: AccessListFormState): ClientRow[] {
  return state.clients.filter((c) => !isBlankClient(c));
}

export type FormProblem = { tab: AccessListTab; message: string };

/**
 * First problem that would make the save fail, or `null` when it is ready.
 * Returning the tab lets the dialog reveal the offending field rather than
 * reporting an error on a panel the user cannot see.
 */
export function validateAccessListForm(state: AccessListFormState): FormProblem | null {
  if (!state.name.trim()) {
    return { tab: "details", message: "Enter a name for the access list." };
  }

  const users = submittableUsers(state);
  const seen = new Set<string>();
  for (const user of users) {
    const username = user.username.trim();
    if (!username) {
      return { tab: "authorization", message: "Enter a username for every user." };
    }
    if (user.id === undefined && !user.password) {
      return { tab: "authorization", message: `Enter a password for “${username}”.` };
    }
    if (seen.has(username)) {
      return {
        tab: "authorization",
        message: `“${username}” is listed twice — usernames must be unique.`,
      };
    }
    seen.add(username);
  }

  for (const rule of submittableClients(state)) {
    if (!normalizeAddress(rule.address)) {
      return { tab: "access", message: "Enter an IP, CIDR, or “all” for every rule." };
    }
  }

  return null;
}

function clientPayload(state: AccessListFormState) {
  return submittableClients(state).map((c) => ({
    address: normalizeAddress(c.address),
    directive: c.directive,
  }));
}

export function buildCreatePayload(state: AccessListFormState): AccessListCreate {
  return {
    name: state.name.trim(),
    satisfy_any: state.satisfyAny,
    pass_auth: state.passAuth,
    auth_users: submittableUsers(state).map((u) => ({
      username: u.username.trim(),
      password: u.password,
    })),
    clients: clientPayload(state),
  };
}

/**
 * Both collections are always sent, so a row the user deleted is actually
 * removed — the backend treats a present key as a full replacement. The
 * password is omitted for an existing user left untouched, which tells the
 * backend to keep their stored hash.
 */
export function buildUpdatePayload(state: AccessListFormState): AccessListUpdate {
  return {
    name: state.name.trim(),
    satisfy_any: state.satisfyAny,
    pass_auth: state.passAuth,
    auth_users: submittableUsers(state).map((u) =>
      u.password
        ? { username: u.username.trim(), password: u.password }
        : { username: u.username.trim() },
    ),
    clients: clientPayload(state),
  };
}
