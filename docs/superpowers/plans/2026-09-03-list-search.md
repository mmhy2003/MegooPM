# List Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put a search box on all eleven list views, so an operator with sixty proxy hosts can find one without reading the table.

**Architecture:** One shared `SearchInput` component and one pure `filterBySearch` helper cover the nine views whose data already arrives as a complete array. CrowdSec's Decisions and Alerts tabs are server-paginated, so they instead send a `q` query parameter that the backend applies *before* `paginate`, keeping `total` equal to the filtered count.

**Tech Stack:** Next.js 16 / React 19 / TypeScript / base-ui / Tailwind v4 / lucide-react / vitest + @testing-library on the frontend; FastAPI + Pydantic v2 / pytest on the backend.

**Spec:** `docs/superpowers/specs/2026-09-03-list-search-design.md`

## Global Constraints

- **No new dependency.** Everything here is built from what the repo already has.
- **Matching is case-insensitive substring on a trimmed query.** Not fuzzy, not prefix-only, not ranked.
- **Every list gets two distinct empty states**: "nothing matches *foo*" (with a way to clear the search) and the existing "nothing here yet". Never one message for both.
- **`filterBySearch` is the only client-side matcher.** A view that hand-rolls `.filter()` re-opens the drift this design exists to prevent.
- **Refinement on the spec's signature:** the spec writes `fields: (item: T) => string[]`. The real schemas have nullable columns (`Decision.scenario`, `CustomPageSummary.description`), so the implemented type is `(item: T) => (string | null | undefined)[]`. The spec's reason for a string-returning callback still holds — the helper never guesses how to stringify a number, date or enum; the caller does.
- **Per-view test coverage is two cases, not three.** The spec asks each view
  for "typing narrows / clearing restores / filtered-empty is distinguishable".
  Tasks 4–7 carry the first and third. *Clearing restores* is proved once in
  Task 3 and once in Task 2's component tests, and it is structural everywhere
  else — `visible` is recomputed from `query`, so a view that narrows correctly
  cannot fail to restore. Six more near-identical tests would buy nothing.
- **Frontend commands** run from `frontend/`: `npm test`, `npm run typecheck`, `npm run lint`.
- **Backend tests cannot run natively on Windows** (`app/services/cluster/locks.py` imports `fcntl`). Run them in a throwaway container from the built `megoopm-backend` image — the exact recipe is in Task 8, Step 2.

---

### Task 1: The `filterBySearch` helper

The pure function every client-side list filter calls. It carries all the
matching risk, so it takes the heaviest test coverage in the plan.

**Files:**
- Create: `frontend/src/lib/search.ts`
- Test: `frontend/src/lib/search.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `filterBySearch<T>(items: T[], query: string, fields: (item: T) => (string | null | undefined)[]): T[]` — returns the items whose fields contain the trimmed, lower-cased query as a substring; returns `items` unchanged when the trimmed query is empty.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/search.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { filterBySearch } from "@/lib/search";

interface Row {
  name: string;
  domains: string[];
  note: string | null;
}

const ROWS: Row[] = [
  { name: "API gateway", domains: ["api.example.com", "www.example.com"], note: null },
  { name: "Blog", domains: ["blog.internal"], note: "staging only" },
];

const fields = (r: Row) => [r.name, ...r.domains, r.note];

describe("filterBySearch", () => {
  it("returns every row for an empty query", () => {
    expect(filterBySearch(ROWS, "", fields)).toEqual(ROWS);
  });

  it("treats a whitespace-only query as no query", () => {
    // An operator who selects the box and hits space must not see an empty table.
    expect(filterBySearch(ROWS, "   ", fields)).toEqual(ROWS);
  });

  it("ignores case on both sides", () => {
    expect(filterBySearch(ROWS, "API GATEWAY", fields)).toEqual([ROWS[0]]);
    expect(filterBySearch(ROWS, "api gateway", fields)).toEqual([ROWS[0]]);
  });

  it("matches a substring, not just a prefix", () => {
    // The common case: an operator remembers the middle of a domain.
    expect(filterBySearch(ROWS, "example", fields)).toEqual([ROWS[0]]);
  });

  it("matches inside an array field", () => {
    expect(filterBySearch(ROWS, "blog.internal", fields)).toEqual([ROWS[1]]);
  });

  it("matches a field that is not the first one", () => {
    expect(filterBySearch(ROWS, "staging", fields)).toEqual([ROWS[1]]);
  });

  it("returns nothing when nothing matches", () => {
    expect(filterBySearch(ROWS, "nonesuch", fields)).toEqual([]);
  });

  it("skips null and undefined fields instead of throwing", () => {
    // ROWS[0].note is null; a naive `.toLowerCase()` would throw here.
    expect(() => filterBySearch(ROWS, "note", fields)).not.toThrow();
    expect(filterBySearch(ROWS, "note", fields)).toEqual([]);
  });

  it("trims the query before matching", () => {
    expect(filterBySearch(ROWS, "  blog  ", fields)).toEqual([ROWS[1]]);
  });

  it("does not mutate the input array", () => {
    const copy = [...ROWS];
    filterBySearch(ROWS, "blog", fields);
    expect(ROWS).toEqual(copy);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/search.test.ts`
Expected: FAIL — `Failed to resolve import "@/lib/search"`.

- [ ] **Step 3: Write the implementation**

Create `frontend/src/lib/search.ts`:

```ts
/**
 * The one substring matcher behind every list page's search box.
 *
 * Shared rather than hand-rolled per view because eleven separate `.filter()`
 * calls would drift in exactly the places that matter: whether case is
 * folded, whether the query is trimmed, and whether an array column like
 * `domain_names` is searched at all.
 *
 * Matching is a case-insensitive substring, not fuzzy: an operator typing
 * `api.example.com` wants that host, and six ranked near-misses are harder to
 * trust than a result that either contains the text or does not.
 */
export function filterBySearch<T>(
  items: T[],
  query: string,
  fields: (item: T) => (string | null | undefined)[],
): T[] {
  const needle = query.trim().toLowerCase();
  // An empty or whitespace-only box is not a filter that matches nothing — it
  // is no filter at all.
  if (!needle) return items;
  return items.filter((item) =>
    fields(item).some(
      (field) => field != null && field.toLowerCase().includes(needle),
    ),
  );
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/lib/search.test.ts`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/search.ts frontend/src/lib/search.test.ts
git commit -m "feat(search): the one substring matcher every list page shares

Eleven hand-rolled .filter() calls would drift in whether they fold case,
trim the query, or search an array column at all. One function, tested once.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The `SearchInput` component

A labelled text box with a search icon and a clear button. Every list page
mounts exactly one, so its accessible name is the page's name.

**Files:**
- Create: `frontend/src/components/ui/search-input.tsx`
- Test: `frontend/src/components/ui/search-input.test.tsx`

**Interfaces:**
- Consumes: `Input` from `@/components/ui/input`, `Button` from `@/components/ui/button`, `cn` from `@/lib/utils`.
- Produces: `SearchInput({ value, onValueChange, label, placeholder?, className? })` — a controlled input. `label` becomes the input's accessible name and, lower-cased, the clear button's ("Clear search proxy hosts"). The clear button renders only when `value` is non-empty.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/ui/search-input.test.tsx`:

```tsx
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SearchInput } from "@/components/ui/search-input";

/** A controlled input needs an owner; without one, typing shows one character. */
function Harness({ initial = "" }: { initial?: string }) {
  const [value, setValue] = useState(initial);
  return <SearchInput value={value} onValueChange={setValue} label="Search proxy hosts" />;
}

afterEach(() => cleanup());

describe("SearchInput", () => {
  it("is reachable by its accessible name", () => {
    render(<Harness />);
    expect(screen.getByRole("searchbox", { name: "Search proxy hosts" })).toBeInTheDocument();
  });

  it("reports every keystroke", async () => {
    const user = userEvent.setup();
    const onValueChange = vi.fn();
    render(<SearchInput value="" onValueChange={onValueChange} label="Search proxy hosts" />);

    await user.type(screen.getByRole("searchbox"), "a");

    expect(onValueChange).toHaveBeenCalledWith("a");
  });

  it("shows no clear button while the box is empty", () => {
    render(<Harness />);
    expect(screen.queryByRole("button", { name: /clear/i })).not.toBeInTheDocument();
  });

  it("offers a clear button once there is something to clear", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.type(screen.getByRole("searchbox"), "api");

    expect(
      screen.getByRole("button", { name: "Clear search proxy hosts" }),
    ).toBeInTheDocument();
  });

  it("empties the box when cleared", async () => {
    const user = userEvent.setup();
    render(<Harness initial="api" />);

    await user.click(screen.getByRole("button", { name: "Clear search proxy hosts" }));

    expect(screen.getByRole("searchbox")).toHaveValue("");
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/ui/search-input.test.tsx`
Expected: FAIL — `Failed to resolve import "@/components/ui/search-input"`.

- [ ] **Step 3: Write the implementation**

Create `frontend/src/components/ui/search-input.tsx`:

```tsx
"use client";

import { Search, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/**
 * The search box shared by every list page.
 *
 * `type="search"` for the `searchbox` role, with WebKit's native cancel
 * button hidden — two clear buttons on one input is worse than none.
 */
export function SearchInput({
  value,
  onValueChange,
  label,
  placeholder = "Search",
  className,
}: {
  value: string;
  onValueChange: (next: string) => void;
  /** Accessible name. One box per page, so name it after the page. */
  label: string;
  placeholder?: string;
  className?: string;
}) {
  return (
    <div className={cn("relative w-full sm:max-w-xs", className)}>
      <Search
        aria-hidden="true"
        className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
      />
      <Input
        type="search"
        aria-label={label}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onValueChange(e.target.value)}
        className="pr-8 pl-8 [&::-webkit-search-cancel-button]:hidden"
      />
      {value ? (
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={`Clear ${label.toLowerCase()}`}
          className="absolute top-1/2 right-0.5 -translate-y-1/2"
          onClick={() => onValueChange("")}
        >
          <X />
        </Button>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/ui/search-input.test.tsx`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/search-input.tsx frontend/src/components/ui/search-input.test.tsx
git commit -m "feat(search): shared search box with a clear button

WebKit's native cancel button is hidden: two clear buttons on one input is
worse than none.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Proxy Hosts — the adoption pattern

The first view to adopt search. Every later view repeats this shape, so get
the two empty states right here.

**Files:**
- Modify: `frontend/src/components/proxy-hosts/proxy-hosts-view.tsx`
- Create: `frontend/src/components/proxy-hosts/proxy-hosts-view.test.tsx`

**Interfaces:**
- Consumes: `filterBySearch` from `@/lib/search`; `SearchInput` from `@/components/ui/search-input`.
- Produces: nothing other views import.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/proxy-hosts/proxy-hosts-view.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  accessLists,
  certificates,
  proxyHosts,
  upstreams,
  type ProxyHost,
} from "@/lib/api";
import { ProxyHostsView } from "@/components/proxy-hosts/proxy-hosts-view";

function makeHost(over: Partial<ProxyHost> = {}): ProxyHost {
  return {
    id: 1,
    domain_names: ["api.example.com"],
    forward_scheme: "http",
    forward_host: "10.0.0.5",
    forward_port: 8080,
    upstream_id: null,
    access_list_id: null,
    certificate_id: null,
    enabled: true,
    ssl_forced: false,
    http2_support: false,
    hsts_enabled: false,
    hsts_subdomains: false,
    block_exploits: false,
    caching_enabled: false,
    allow_websocket_upgrade: false,
    advanced_config: "",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    ...over,
  } as ProxyHost;
}

async function renderView(rows: ProxyHost[]) {
  vi.spyOn(proxyHosts, "list").mockResolvedValue(rows);
  vi.spyOn(upstreams, "list").mockResolvedValue([]);
  vi.spyOn(accessLists, "list").mockResolvedValue([]);
  vi.spyOn(certificates, "list").mockResolvedValue([]);
  render(<ProxyHostsView />);
  await screen.findByRole("searchbox", { name: "Search proxy hosts" });
}

const ROWS = [
  makeHost({ id: 1, domain_names: ["api.example.com"], forward_host: "10.0.0.5" }),
  makeHost({ id: 2, domain_names: ["blog.internal"], forward_host: "10.0.0.6" }),
];

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ProxyHostsView search", () => {
  it("narrows the table to matching hosts", async () => {
    const user = userEvent.setup();
    await renderView(ROWS);

    await user.type(screen.getByRole("searchbox"), "blog");

    expect(screen.getByText("blog.internal")).toBeInTheDocument();
    expect(screen.queryByText("api.example.com")).not.toBeInTheDocument();
  });

  it("matches the forward target as well as the domain", async () => {
    const user = userEvent.setup();
    await renderView(ROWS);

    await user.type(screen.getByRole("searchbox"), "10.0.0.6");

    expect(screen.getByText("blog.internal")).toBeInTheDocument();
    expect(screen.queryByText("api.example.com")).not.toBeInTheDocument();
  });

  it("restores every row when the search is cleared", async () => {
    const user = userEvent.setup();
    await renderView(ROWS);
    await user.type(screen.getByRole("searchbox"), "blog");

    await user.click(screen.getByRole("button", { name: "Clear search proxy hosts" }));

    expect(screen.getByText("api.example.com")).toBeInTheDocument();
    expect(screen.getByText("blog.internal")).toBeInTheDocument();
  });

  it("says a filter is hiding the rows, not that there are none", async () => {
    // The bug this exists for: a filtered-empty table that reads like an empty
    // install sends an operator hunting for a bug that is a stale search box.
    const user = userEvent.setup();
    await renderView(ROWS);

    await user.type(screen.getByRole("searchbox"), "nonesuch");

    expect(screen.getByText(/no proxy hosts match/i)).toBeInTheDocument();
    expect(screen.queryByText(/no proxy hosts yet/i)).not.toBeInTheDocument();
  });

  it("still says 'none yet' when the instance really is empty", async () => {
    await renderView([]);
    expect(screen.getByText(/no proxy hosts yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/no proxy hosts match/i)).not.toBeInTheDocument();
  });

  it("offers a way out of a filter that matches nothing", async () => {
    const user = userEvent.setup();
    await renderView(ROWS);
    await user.type(screen.getByRole("searchbox"), "nonesuch");

    await user.click(screen.getByRole("button", { name: /clear search/i }));

    expect(screen.getByText("api.example.com")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/proxy-hosts/proxy-hosts-view.test.tsx`
Expected: FAIL — `Unable to find role="searchbox"`.

- [ ] **Step 3: Add the search state and the filtered list**

In `frontend/src/components/proxy-hosts/proxy-hosts-view.tsx`, add to the imports:

```tsx
import { SearchInput } from "@/components/ui/search-input";
import { filterBySearch } from "@/lib/search";
```

Add the state below `const [deleteHost, setDeleteHost] = useState<ProxyHost | null>(null);`:

```tsx
const [query, setQuery] = useState("");
```

Add the derived list beside the existing `listsById` memo:

```tsx
// Domains and the forward target: what an operator remembers about a host.
// Not the status or scheme columns — matching those makes `active` and `http`
// return half the table.
const visible = useMemo(
  () => filterBySearch(hosts, query, (h) => [...h.domain_names, h.forward_host]),
  [hosts, query],
);
```

- [ ] **Step 4: Put the box in the toolbar**

Replace:

```tsx
        <div className="flex justify-end">
          <Button size="sm" onClick={() => setHostDialog({ open: true, host: null })}>
            <Plus /> New proxy host
          </Button>
        </div>
```

with:

```tsx
        <div className="flex flex-wrap items-center justify-between gap-2">
          <SearchInput
            value={query}
            onValueChange={setQuery}
            label="Search proxy hosts"
            placeholder="Domain or forward host"
          />
          <Button size="sm" onClick={() => setHostDialog({ open: true, host: null })}>
            <Plus /> New proxy host
          </Button>
        </div>
```

- [ ] **Step 5: Give the table both empty states**

Replace:

```tsx
              ) : hosts.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                    No proxy hosts yet. Create a pool, then add a host that forwards to it.
                  </TableCell>
                </TableRow>
              ) : (
                hosts.map((host) => {
```

with:

```tsx
              ) : visible.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                    {query.trim() ? (
                      <>
                        No proxy hosts match “{query.trim()}”.{" "}
                        <Button
                          variant="link"
                          size="sm"
                          className="h-auto p-0 align-baseline"
                          onClick={() => setQuery("")}
                        >
                          Clear search
                        </Button>
                      </>
                    ) : (
                      "No proxy hosts yet. Create a pool, then add a host that forwards to it."
                    )}
                  </TableCell>
                </TableRow>
              ) : (
                visible.map((host) => {
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/proxy-hosts/proxy-hosts-view.test.tsx`
Expected: PASS, 6 tests.

- [ ] **Step 7: Typecheck and lint**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/proxy-hosts/proxy-hosts-view.tsx frontend/src/components/proxy-hosts/proxy-hosts-view.test.tsx
git commit -m "feat(proxy-hosts): search by domain or forward host

Two empty states, not one: a filtered-empty table that reads like an empty
install sends an operator hunting for a bug that is a stale search box.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Upstream Pools and Access Lists

Both are name-first lists. Pools also match their backend hosts, because
"which pool holds 10.0.0.6?" is the question an operator actually has.

**Files:**
- Modify: `frontend/src/components/upstreams/upstreams-view.tsx`
- Modify: `frontend/src/components/access-lists/access-lists-view.tsx`
- Test: `frontend/src/components/upstreams/upstreams-view.test.tsx` (exists — append)
- Create: `frontend/src/components/access-lists/access-lists-view.test.tsx`

**Interfaces:**
- Consumes: `filterBySearch` from `@/lib/search`; `SearchInput` from `@/components/ui/search-input`.
- Produces: nothing other views import.

- [ ] **Step 1: Write the failing test for Upstream Pools**

Append to `frontend/src/components/upstreams/upstreams-view.test.tsx` (reuse the
file's existing imports; add `userEvent` if it is missing):

```tsx
describe("UpstreamsView search", () => {
  it("matches a pool by name and by backend host", async () => {
    const user = userEvent.setup();
    vi.spyOn(upstreams, "list").mockResolvedValue([
      {
        id: 1,
        name: "api-pool",
        description: "",
        lb_method: "round_robin",
        context: "http",
        enabled: true,
        backends: [
          {
            id: 1,
            upstream_id: 1,
            host: "10.0.0.5",
            port: 8080,
            weight: 1,
            max_fails: 1,
            fail_timeout_seconds: 10,
            backup: false,
            down: false,
            enabled: true,
            created_at: "2026-09-01T00:00:00Z",
            updated_at: "2026-09-01T00:00:00Z",
          },
        ],
        created_at: "2026-09-01T00:00:00Z",
        updated_at: "2026-09-01T00:00:00Z",
      },
      {
        id: 2,
        name: "blog-pool",
        description: "",
        lb_method: "round_robin",
        context: "http",
        enabled: true,
        backends: [],
        created_at: "2026-09-01T00:00:00Z",
        updated_at: "2026-09-01T00:00:00Z",
      },
    ]);
    render(<UpstreamsView />);
    await screen.findByRole("searchbox", { name: "Search upstream pools" });

    await user.type(screen.getByRole("searchbox"), "10.0.0.5");

    expect(screen.getByText("api-pool")).toBeInTheDocument();
    expect(screen.queryByText("blog-pool")).not.toBeInTheDocument();
  });

  it("distinguishes a filtered-empty table from an empty instance", async () => {
    const user = userEvent.setup();
    vi.spyOn(upstreams, "list").mockResolvedValue([]);
    render(<UpstreamsView />);
    await screen.findByRole("searchbox", { name: "Search upstream pools" });
    expect(screen.getByText(/no upstream pools yet/i)).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox"), "nonesuch");

    expect(screen.getByText(/no upstream pools match/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/components/upstreams/upstreams-view.test.tsx`
Expected: FAIL — `Unable to find role="searchbox"`.

- [ ] **Step 3: Wire Upstream Pools**

In `frontend/src/components/upstreams/upstreams-view.tsx` add the imports:

```tsx
import { SearchInput } from "@/components/ui/search-input";
import { filterBySearch } from "@/lib/search";
```

Add `useMemo` to the existing `react` import if it is not already there, then
add below `const [deletePool, setDeletePool] = useState<Upstream | null>(null);`:

```tsx
const [query, setQuery] = useState("");

// Backend hosts too: "which pool holds 10.0.0.6?" is the real question.
const visible = useMemo(
  () => filterBySearch(pools, query, (p) => [p.name, ...p.backends.map((b) => b.host)]),
  [pools, query],
);
```

Replace the toolbar:

```tsx
        <div className="flex justify-end">
          <Button size="sm" onClick={() => setPoolDialog({ open: true, pool: null })}>
            <Plus /> New upstream pool
          </Button>
        </div>
```

with:

```tsx
        <div className="flex flex-wrap items-center justify-between gap-2">
          <SearchInput
            value={query}
            onValueChange={setQuery}
            label="Search upstream pools"
            placeholder="Pool name or backend host"
          />
          <Button size="sm" onClick={() => setPoolDialog({ open: true, pool: null })}>
            <Plus /> New upstream pool
          </Button>
        </div>
```

Replace the empty branch:

```tsx
              ) : pools.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                    No upstream pools yet. Create one to define a load-balanced backend set.
                  </TableCell>
                </TableRow>
              ) : (
                pools.map((pool) => {
```

with:

```tsx
              ) : visible.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                    {query.trim() ? (
                      <>
                        No upstream pools match “{query.trim()}”.{" "}
                        <Button
                          variant="link"
                          size="sm"
                          className="h-auto p-0 align-baseline"
                          onClick={() => setQuery("")}
                        >
                          Clear search
                        </Button>
                      </>
                    ) : (
                      "No upstream pools yet. Create one to define a load-balanced backend set."
                    )}
                  </TableCell>
                </TableRow>
              ) : (
                visible.map((pool) => {
```

- [ ] **Step 4: Write the failing test for Access Lists**

Create `frontend/src/components/access-lists/access-lists-view.test.tsx`:

```tsx
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
```

- [ ] **Step 5: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/components/access-lists/access-lists-view.test.tsx`
Expected: FAIL — `Unable to find role="searchbox"`.

- [ ] **Step 6: Wire Access Lists**

In `frontend/src/components/access-lists/access-lists-view.tsx` add the imports:

```tsx
import { SearchInput } from "@/components/ui/search-input";
import { filterBySearch } from "@/lib/search";
```

Add `useMemo` to the `react` import if missing, then add below
`const [deleteList, setDeleteList] = useState<AccessList | null>(null);`:

```tsx
const [query, setQuery] = useState("");

// Name only: the users and IP rules are nested collections, and matching
// them would make a search for "10." return every list with a private range.
const visible = useMemo(
  () => filterBySearch(lists, query, (l) => [l.name]),
  [lists, query],
);
```

This view keeps its "New access list" button in the page header, so add a
toolbar row of its own immediately above `<div className="rounded-xl border">`:

```tsx
      <div className="flex flex-wrap items-center justify-between gap-2">
        <SearchInput
          value={query}
          onValueChange={setQuery}
          label="Search access lists"
          placeholder="List name"
        />
      </div>
```

Replace `) : lists.length === 0 ? (` with `) : visible.length === 0 ? (`, and
replace the message inside the following `<TableCell colSpan={6} …>` — the
text currently beginning `No access lists yet. Create one, then attach it to a
proxy host` — with:

```tsx
                  {query.trim() ? (
                    <>
                      No access lists match “{query.trim()}”.{" "}
                      <Button
                        variant="link"
                        size="sm"
                        className="h-auto p-0 align-baseline"
                        onClick={() => setQuery("")}
                      >
                        Clear search
                      </Button>
                    </>
                  ) : (
                    "No access lists yet. Create one, then attach it to a proxy host."
                  )}
```

Then replace `lists.map((list) => (` with `visible.map((list) => (`.

- [ ] **Step 7: Run both test files to verify they pass**

Run: `cd frontend && npx vitest run src/components/upstreams src/components/access-lists`
Expected: PASS, including the pre-existing upstream-dialog and upstreams-view tests.

- [ ] **Step 8: Typecheck and lint**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/upstreams frontend/src/components/access-lists
git commit -m "feat(upstreams,access-lists): search the pool and list tables

A pool also matches its backend hosts, because 'which pool holds 10.0.0.6?'
is the question an operator actually has.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Certificates and Custom Pages

Both keep their "New …" button in the page header, so both gain a toolbar row
of their own above the table.

**Files:**
- Modify: `frontend/src/components/certificates/certificates-view.tsx`
- Modify: `frontend/src/components/custom-pages/custom-pages-view.tsx`
- Create: `frontend/src/components/certificates/certificates-view.test.tsx`
- Test: `frontend/src/components/custom-pages/custom-pages-view.test.tsx` (exists — append)

**Interfaces:**
- Consumes: `filterBySearch` from `@/lib/search`; `SearchInput` from `@/components/ui/search-input`.
- Produces: nothing other views import.

- [ ] **Step 1: Write the failing test for Certificates**

Create `frontend/src/components/certificates/certificates-view.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { certificates, dnsCredentials, dnsProviders, type Certificate } from "@/lib/api";
import { CertificatesView } from "@/components/certificates/certificates-view";

function makeCert(over: Partial<Certificate> = {}): Certificate {
  return {
    id: 1,
    name: "wildcard",
    domain_names: ["*.example.com"],
    provider: "letsencrypt",
    challenge: "dns-01",
    dns_provider: null,
    status: "active",
    expires_on: "2027-01-01T00:00:00Z",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    ...over,
  } as Certificate;
}

async function renderView(rows: Certificate[]) {
  vi.spyOn(certificates, "list").mockResolvedValue(rows);
  // The DNS providers tab panel mounts alongside the certificates one, and it
  // fetches on mount — unmocked, those two requests reject into the console.
  vi.spyOn(dnsCredentials, "list").mockResolvedValue([]);
  vi.spyOn(dnsProviders, "catalog").mockResolvedValue([]);
  render(<CertificatesView />);
  await screen.findByRole("searchbox", { name: "Search certificates" });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("CertificatesView search", () => {
  it("matches a certificate by one of its domains", async () => {
    const user = userEvent.setup();
    await renderView([
      makeCert({ id: 1, name: "wildcard", domain_names: ["*.example.com"] }),
      makeCert({ id: 2, name: "internal", domain_names: ["blog.internal"] }),
    ]);

    await user.type(screen.getByRole("searchbox"), "blog.internal");

    expect(screen.getByText("internal")).toBeInTheDocument();
    expect(screen.queryByText("wildcard")).not.toBeInTheDocument();
  });

  it("distinguishes a filtered-empty table from an empty instance", async () => {
    const user = userEvent.setup();
    await renderView([]);
    expect(screen.getByText(/no certificates yet/i)).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox"), "nonesuch");

    expect(screen.getByText(/no certificates match/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/components/certificates/certificates-view.test.tsx`
Expected: FAIL — `Unable to find role="searchbox"`.

- [ ] **Step 3: Wire Certificates**

In `frontend/src/components/certificates/certificates-view.tsx` add the imports:

```tsx
import { SearchInput } from "@/components/ui/search-input";
import { filterBySearch } from "@/lib/search";
```

Add `useMemo` to the `react` import if missing, then add below
`const [pending, setPending] = useState<PendingTask[]>([]);`:

```tsx
const [query, setQuery] = useState("");

// Name and domains — not the provider or status columns, which would make
// "active" and "custom" each return a third of the table.
const visible = useMemo(
  () => filterBySearch(certs, query, (c) => [c.name, ...c.domain_names]),
  [certs, query],
);
```

Insert a toolbar row immediately above the `<div className="rounded-xl border">`
that wraps the certificates `<Table>` (the one whose header row starts
`<TableHead>Name</TableHead>` followed by `<TableHead>Domains</TableHead>` —
**not** the DNS providers panel's table):

```tsx
      <div className="flex flex-wrap items-center justify-between gap-2">
        <SearchInput
          value={query}
          onValueChange={setQuery}
          label="Search certificates"
          placeholder="Certificate name or domain"
        />
      </div>
```

Replace `) : certs.length === 0 ? (` with `) : visible.length === 0 ? (`, and
replace the message inside the following `<TableCell colSpan={7} …>` with:

```tsx
                  {query.trim() ? (
                    <>
                      No certificates match “{query.trim()}”.{" "}
                      <Button
                        variant="link"
                        size="sm"
                        className="h-auto p-0 align-baseline"
                        onClick={() => setQuery("")}
                      >
                        Clear search
                      </Button>
                    </>
                  ) : (
                    "No certificates yet. Request one from Let’s Encrypt or upload your own."
                  )}
```

Then replace `certs.map((cert) => {` with `visible.map((cert) => {`.

- [ ] **Step 4: Write the failing test for Custom Pages**

Append to `frontend/src/components/custom-pages/custom-pages-view.test.tsx`
(add `customPages` to its `@/lib/api` import and `userEvent` to its imports if
they are not already present):

```tsx
describe("CustomPagesView search", () => {
  it("matches a page by name or description", async () => {
    const user = userEvent.setup();
    vi.spyOn(customPages, "list").mockResolvedValue([
      {
        id: 1,
        name: "Maintenance",
        description: "shown during deploys",
        size_bytes: 1024,
        created_at: "2026-09-01T00:00:00Z",
        updated_at: "2026-09-01T00:00:00Z",
      },
      {
        id: 2,
        name: "Banned",
        description: null,
        size_bytes: 512,
        created_at: "2026-09-01T00:00:00Z",
        updated_at: "2026-09-01T00:00:00Z",
      },
    ]);
    render(<CustomPagesView />);
    await screen.findByRole("searchbox", { name: "Search custom pages" });

    await user.type(screen.getByRole("searchbox"), "deploys");

    expect(screen.getByText("Maintenance")).toBeInTheDocument();
    expect(screen.queryByText("Banned")).not.toBeInTheDocument();
  });

  it("does not choke on a page with no description", async () => {
    // `description` is nullable; a naive matcher throws on the null row.
    const user = userEvent.setup();
    vi.spyOn(customPages, "list").mockResolvedValue([
      {
        id: 2,
        name: "Banned",
        description: null,
        size_bytes: 512,
        created_at: "2026-09-01T00:00:00Z",
        updated_at: "2026-09-01T00:00:00Z",
      },
    ]);
    render(<CustomPagesView />);
    await screen.findByRole("searchbox", { name: "Search custom pages" });

    await user.type(screen.getByRole("searchbox"), "banned");

    expect(screen.getByText("Banned")).toBeInTheDocument();
  });
});
```

- [ ] **Step 5: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/components/custom-pages/custom-pages-view.test.tsx`
Expected: FAIL — `Unable to find role="searchbox"`.

- [ ] **Step 6: Wire Custom Pages**

In `frontend/src/components/custom-pages/custom-pages-view.tsx` add the imports:

```tsx
import { SearchInput } from "@/components/ui/search-input";
import { filterBySearch } from "@/lib/search";
```

Add `useMemo` to the `react` import if missing, then add below
`const [deletePage, setDeletePage] = useState<CustomPageSummary | null>(null);`:

```tsx
const [query, setQuery] = useState("");

const visible = useMemo(
  () => filterBySearch(pages, query, (p) => [p.name, p.description]),
  [pages, query],
);
```

Insert a toolbar row immediately above `<div className="rounded-xl border">`:

```tsx
      <div className="flex flex-wrap items-center justify-between gap-2">
        <SearchInput
          value={query}
          onValueChange={setQuery}
          label="Search custom pages"
          placeholder="Page name or description"
        />
      </div>
```

Replace `) : pages.length === 0 ? (` with `) : visible.length === 0 ? (`, and
replace the message inside the following `<TableCell colSpan={5} …>` with:

```tsx
                  {query.trim() ? (
                    <>
                      No custom pages match “{query.trim()}”.{" "}
                      <Button
                        variant="link"
                        size="sm"
                        className="h-auto p-0 align-baseline"
                        onClick={() => setQuery("")}
                      >
                        Clear search
                      </Button>
                    </>
                  ) : (
                    "No custom pages yet. Create one to design a page you can point a host at."
                  )}
```

Then replace `pages.map((page) => (` with `visible.map((page) => (`.

- [ ] **Step 7: Run both test files to verify they pass**

Run: `cd frontend && npx vitest run src/components/certificates src/components/custom-pages`
Expected: PASS, including the pre-existing dialog and editor tests.

- [ ] **Step 8: Typecheck and lint**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/certificates frontend/src/components/custom-pages
git commit -m "feat(certificates,custom-pages): search both tables

Both keep their New button in the page header, so both gain a toolbar row of
their own rather than moving the button.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Streams, Redirection Hosts and 404 Hosts

Three views with the same `flex justify-end` toolbar as Proxy Hosts. Streams
is the one that converts a numeric column to a string.

**Files:**
- Modify: `frontend/src/components/streams/streams-view.tsx`
- Modify: `frontend/src/components/redirection-hosts/redirection-hosts-view.tsx`
- Modify: `frontend/src/components/dead-hosts/dead-hosts-view.tsx`
- Create: `frontend/src/components/streams/streams-view.test.tsx`
- Create: `frontend/src/components/redirection-hosts/redirection-hosts-view.test.tsx`
- Test: `frontend/src/components/dead-hosts/dead-hosts-view.test.tsx` (exists — append)

**Interfaces:**
- Consumes: `filterBySearch` from `@/lib/search`; `SearchInput` from `@/components/ui/search-input`.
- Produces: nothing other views import.

- [ ] **Step 1: Write the failing test for Streams**

Create `frontend/src/components/streams/streams-view.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { certificates, streams, upstreams, type Stream } from "@/lib/api";
import { StreamsView } from "@/components/streams/streams-view";

function makeStream(over: Partial<Stream> = {}): Stream {
  return {
    id: 1,
    incoming_port: 5432,
    forward_host: "10.0.0.5",
    forward_port: 5432,
    tcp_forwarding: true,
    udp_forwarding: false,
    certificate_id: null,
    upstream_id: null,
    enabled: true,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    ...over,
  } as Stream;
}

async function renderView(rows: Stream[]) {
  vi.spyOn(streams, "list").mockResolvedValue(rows);
  vi.spyOn(certificates, "list").mockResolvedValue([]);
  vi.spyOn(upstreams, "list").mockResolvedValue([]);
  render(<StreamsView />);
  await screen.findByRole("searchbox", { name: "Search streams" });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("StreamsView search", () => {
  it("matches the incoming port, which is a number", async () => {
    // The page stringifies the port; the helper only ever sees strings.
    const user = userEvent.setup();
    await renderView([
      makeStream({ id: 1, incoming_port: 5432, forward_host: "10.0.0.5" }),
      makeStream({ id: 2, incoming_port: 6379, forward_host: "10.0.0.6" }),
    ]);

    await user.type(screen.getByRole("searchbox"), "6379");

    expect(screen.getByText(/6379/)).toBeInTheDocument();
    expect(screen.queryByText(/5432/)).not.toBeInTheDocument();
  });

  it("distinguishes a filtered-empty table from an empty instance", async () => {
    const user = userEvent.setup();
    await renderView([]);
    expect(screen.getByText(/no streams yet/i)).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox"), "nonesuch");

    expect(screen.getByText(/no streams match/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/components/streams/streams-view.test.tsx`
Expected: FAIL — `Unable to find role="searchbox"`.

- [ ] **Step 3: Wire Streams**

In `frontend/src/components/streams/streams-view.tsx` add the imports:

```tsx
import { SearchInput } from "@/components/ui/search-input";
import { filterBySearch } from "@/lib/search";
```

Add `useMemo` to the `react` import if missing, then add below
`const [pools, setPools] = useState<Upstream[]>([]);`:

```tsx
const [query, setQuery] = useState("");

// `incoming_port` is a number, so the page stringifies it here: the helper
// stays string-only and never guesses how to render a number for matching.
const visible = useMemo(
  () => filterBySearch(rows, query, (s) => [String(s.incoming_port), s.forward_host]),
  [rows, query],
);
```

Replace the toolbar:

```tsx
        <div className="flex justify-end">
          <Button size="sm" onClick={() => setDialog({ open: true, stream: null })}>
            <Plus /> New stream
          </Button>
        </div>
```

with:

```tsx
        <div className="flex flex-wrap items-center justify-between gap-2">
          <SearchInput
            value={query}
            onValueChange={setQuery}
            label="Search streams"
            placeholder="Port or forward host"
          />
          <Button size="sm" onClick={() => setDialog({ open: true, stream: null })}>
            <Plus /> New stream
          </Button>
        </div>
```

Replace the empty branch:

```tsx
              ) : rows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                    No streams yet. Create one to forward a TCP/UDP port to a backend.
                  </TableCell>
                </TableRow>
              ) : (
                rows.map((stream) => (
```

with:

```tsx
              ) : visible.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                    {query.trim() ? (
                      <>
                        No streams match “{query.trim()}”.{" "}
                        <Button
                          variant="link"
                          size="sm"
                          className="h-auto p-0 align-baseline"
                          onClick={() => setQuery("")}
                        >
                          Clear search
                        </Button>
                      </>
                    ) : (
                      "No streams yet. Create one to forward a TCP/UDP port to a backend."
                    )}
                  </TableCell>
                </TableRow>
              ) : (
                visible.map((stream) => (
```

- [ ] **Step 4: Write the failing test for Redirection Hosts**

Create `frontend/src/components/redirection-hosts/redirection-hosts-view.test.tsx`:

```tsx
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
```

- [ ] **Step 5: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/components/redirection-hosts/redirection-hosts-view.test.tsx`
Expected: FAIL — `Unable to find role="searchbox"`.

- [ ] **Step 6: Wire Redirection Hosts**

In `frontend/src/components/redirection-hosts/redirection-hosts-view.tsx` add:

```tsx
import { SearchInput } from "@/components/ui/search-input";
import { filterBySearch } from "@/lib/search";
```

Add `useMemo` to the `react` import if missing, then add below
`const [toDelete, setToDelete] = useState<RedirectionHost | null>(null);`:

```tsx
const [query, setQuery] = useState("");

const visible = useMemo(
  () => filterBySearch(rows, query, (h) => [...h.domain_names, h.forward_domain_name]),
  [rows, query],
);
```

Replace the toolbar:

```tsx
        <div className="flex justify-end">
          <Button size="sm" onClick={() => setDialog({ open: true, host: null })}>
            <Plus /> New redirection host
          </Button>
        </div>
```

with:

```tsx
        <div className="flex flex-wrap items-center justify-between gap-2">
          <SearchInput
            value={query}
            onValueChange={setQuery}
            label="Search redirection hosts"
            placeholder="Domain or redirect target"
          />
          <Button size="sm" onClick={() => setDialog({ open: true, host: null })}>
            <Plus /> New redirection host
          </Button>
        </div>
```

Replace the empty branch:

```tsx
              ) : rows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                    No redirection hosts yet. Create one to redirect domains elsewhere.
                  </TableCell>
                </TableRow>
              ) : (
                rows.map((host) => (
```

with:

```tsx
              ) : visible.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                    {query.trim() ? (
                      <>
                        No redirection hosts match “{query.trim()}”.{" "}
                        <Button
                          variant="link"
                          size="sm"
                          className="h-auto p-0 align-baseline"
                          onClick={() => setQuery("")}
                        >
                          Clear search
                        </Button>
                      </>
                    ) : (
                      "No redirection hosts yet. Create one to redirect domains elsewhere."
                    )}
                  </TableCell>
                </TableRow>
              ) : (
                visible.map((host) => (
```

- [ ] **Step 7: Write the failing test for 404 Hosts**

Append to `frontend/src/components/dead-hosts/dead-hosts-view.test.tsx` (it
already defines `makeDeadHost` and imports `certificates`, `deadHosts`,
`userEvent`, `cleanup` and `vi`):

```tsx
describe("DeadHostsView search", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("narrows the table by domain", async () => {
    const user = userEvent.setup();
    vi.spyOn(deadHosts, "list").mockResolvedValue([
      makeDeadHost({ id: 1, domain_names: ["parked.example.com"] }),
      makeDeadHost({ id: 2, domain_names: ["retired.internal"] }),
    ]);
    vi.spyOn(certificates, "list").mockResolvedValue([]);
    render(<DeadHostsView />);
    await screen.findByRole("searchbox", { name: "Search 404 hosts" });

    await user.type(screen.getByRole("searchbox"), "retired");

    expect(screen.getByText("retired.internal")).toBeInTheDocument();
    expect(screen.queryByText("parked.example.com")).not.toBeInTheDocument();
  });

  it("distinguishes a filtered-empty table from an empty instance", async () => {
    const user = userEvent.setup();
    vi.spyOn(deadHosts, "list").mockResolvedValue([]);
    vi.spyOn(certificates, "list").mockResolvedValue([]);
    render(<DeadHostsView />);
    await screen.findByRole("searchbox", { name: "Search 404 hosts" });
    expect(screen.getByText(/no 404 hosts yet/i)).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox"), "nonesuch");

    expect(screen.getByText(/no 404 hosts match/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 8: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/components/dead-hosts/dead-hosts-view.test.tsx`
Expected: FAIL — `Unable to find role="searchbox"`.

- [ ] **Step 9: Wire 404 Hosts**

In `frontend/src/components/dead-hosts/dead-hosts-view.tsx` add:

```tsx
import { SearchInput } from "@/components/ui/search-input";
import { filterBySearch } from "@/lib/search";
```

Add `useMemo` to the `react` import if missing, then add below
`const [toDelete, setToDelete] = useState<DeadHost | null>(null);`:

```tsx
const [query, setQuery] = useState("");

// Domains only: a 404 host has no forward target to match on.
const visible = useMemo(
  () => filterBySearch(rows, query, (h) => [...h.domain_names]),
  [rows, query],
);
```

Replace the toolbar:

```tsx
        <div className="flex justify-end">
          <Button size="sm" onClick={() => setDialog({ open: true, host: null })}>
            <Plus /> New 404 host
          </Button>
        </div>
```

with:

```tsx
        <div className="flex flex-wrap items-center justify-between gap-2">
          <SearchInput
            value={query}
            onValueChange={setQuery}
            label="Search 404 hosts"
            placeholder="Domain"
          />
          <Button size="sm" onClick={() => setDialog({ open: true, host: null })}>
            <Plus /> New 404 host
          </Button>
        </div>
```

Replace the empty branch:

```tsx
              ) : rows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={4} className="py-10 text-center text-muted-foreground">
                    No 404 hosts yet. Create one to park a domain.
                  </TableCell>
                </TableRow>
              ) : (
                rows.map((host) => (
```

with:

```tsx
              ) : visible.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={4} className="py-10 text-center text-muted-foreground">
                    {query.trim() ? (
                      <>
                        No 404 hosts match “{query.trim()}”.{" "}
                        <Button
                          variant="link"
                          size="sm"
                          className="h-auto p-0 align-baseline"
                          onClick={() => setQuery("")}
                        >
                          Clear search
                        </Button>
                      </>
                    ) : (
                      "No 404 hosts yet. Create one to park a domain."
                    )}
                  </TableCell>
                </TableRow>
              ) : (
                visible.map((host) => (
```

- [ ] **Step 10: Run all three test files to verify they pass**

Run: `cd frontend && npx vitest run src/components/streams src/components/redirection-hosts src/components/dead-hosts`
Expected: PASS, including the pre-existing dialog and toggle tests.

- [ ] **Step 11: Typecheck and lint**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: no errors.

- [ ] **Step 12: Commit**

```bash
git add frontend/src/components/streams frontend/src/components/redirection-hosts frontend/src/components/dead-hosts
git commit -m "feat(streams,redirects,404-hosts): search all three tables

Streams stringifies its incoming port at the call site, so the shared matcher
stays string-only and never guesses how to render a number.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Security → Whitelists

The last client-side list. Its empty state lives inside `WhitelistsTable`, so
the table learns to say which kind of empty it is.

**Files:**
- Modify: `frontend/src/components/security/whitelists-table.tsx`
- Modify: `frontend/src/components/security/security-view.tsx`
- Test: `frontend/src/components/security/whitelists-table.test.tsx` (exists — append)
- Test: `frontend/src/components/security/security-view.test.tsx` (exists — append)

**Interfaces:**
- Consumes: `filterBySearch` from `@/lib/search`; `SearchInput` from `@/components/ui/search-input`.
- Produces: `WhitelistsTable` gains two optional props — `query?: string` and `onClearSearch?: () => void`. When `rows` is empty and `query` is non-blank, the table renders the filtered-empty message instead of the never-had-any one.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/components/security/whitelists-table.test.tsx` (add
`userEvent` and `vi` to its imports if they are not already present):

```tsx
describe("WhitelistsTable empty states", () => {
  const noop = async () => {};

  it("says 'none yet' when nothing is filtered", () => {
    render(
      <WhitelistsTable rows={[]} onToggle={noop} onEdit={() => {}} onDelete={() => {}} />,
    );
    expect(screen.getByText(/no whitelists yet/i)).toBeInTheDocument();
  });

  it("says a search is hiding the rows when one is active", () => {
    render(
      <WhitelistsTable
        rows={[]}
        query="office"
        onClearSearch={() => {}}
        onToggle={noop}
        onEdit={() => {}}
        onDelete={() => {}}
      />,
    );
    expect(screen.getByText(/no whitelists match/i)).toBeInTheDocument();
    expect(screen.queryByText(/no whitelists yet/i)).not.toBeInTheDocument();
  });

  it("offers a way out of a filter that matches nothing", async () => {
    const user = userEvent.setup();
    const onClearSearch = vi.fn();
    render(
      <WhitelistsTable
        rows={[]}
        query="office"
        onClearSearch={onClearSearch}
        onToggle={noop}
        onEdit={() => {}}
        onDelete={() => {}}
      />,
    );

    await user.click(screen.getByRole("button", { name: /clear search/i }));

    expect(onClearSearch).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npx vitest run src/components/security/whitelists-table.test.tsx`
Expected: FAIL — the `query="office"` case still renders "No whitelists yet".

- [ ] **Step 3: Teach the table both empty states**

In `frontend/src/components/security/whitelists-table.tsx`, extend the props:

```tsx
export function WhitelistsTable({
  rows,
  query = "",
  onClearSearch,
  onToggle,
  onEdit,
  onDelete,
}: {
  rows: Whitelist[];
  /** The active search, so the empty state can say which kind of empty it is. */
  query?: string;
  onClearSearch?: () => void;
  onToggle: (row: Whitelist, next: boolean) => Promise<void>;
  onEdit: (row: Whitelist) => void;
  onDelete: (row: Whitelist) => void;
}) {
```

and replace the empty branch:

```tsx
  if (rows.length === 0) {
    return (
      <p className="text-muted-foreground rounded-lg border border-dashed p-8 text-center text-sm">
        No whitelists yet. Add one to stop CrowdSec acting on traffic from an
        address you trust.
      </p>
    );
  }
```

with:

```tsx
  if (rows.length === 0) {
    const searching = query.trim();
    return (
      <p className="text-muted-foreground rounded-lg border border-dashed p-8 text-center text-sm">
        {searching ? (
          <>
            No whitelists match “{searching}”.{" "}
            <Button
              variant="link"
              size="sm"
              className="h-auto p-0 align-baseline"
              onClick={onClearSearch}
            >
              Clear search
            </Button>
          </>
        ) : (
          "No whitelists yet. Add one to stop CrowdSec acting on traffic from an address you trust."
        )}
      </p>
    );
  }
```

`Button` is already imported in this file.

- [ ] **Step 4: Run the table tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/security/whitelists-table.test.tsx`
Expected: PASS.

- [ ] **Step 5: Write the failing test for the tab**

Append to `frontend/src/components/security/security-view.test.tsx`:

Both new `describe` blocks need the same `beforeEach` the file's existing
blocks use — copy it verbatim:

```tsx
  beforeEach(() => {
    vi.spyOn(crowdsec, "health").mockResolvedValue(healthOk as never);
    vi.spyOn(crowdsec, "listDecisions").mockResolvedValue(decisionList(120) as never);
    vi.spyOn(crowdsec, "listAlerts").mockResolvedValue(emptyAlerts as never);
    vi.spyOn(crowdsec, "listWhitelists").mockResolvedValue([] as never);
    vi.spyOn(crowdsec, "whitelistStatus").mockResolvedValue({
      ok: true,
      error: null,
      applied_at: null,
      reload_configured: true,
    } as never);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });
```

`healthOk`, `decisionList` and `emptyAlerts` are already defined at the top of
the file. A test that installs its own `listDecisions` / `listWhitelists` mock
overrides the one from `beforeEach`.


```tsx
describe("SecurityView whitelist search", () => {
  it("narrows the whitelists table", async () => {
    const user = userEvent.setup();
    vi.spyOn(crowdsec, "listWhitelists").mockResolvedValue([
      {
        id: 1,
        name: "office",
        kind: "ip_cidr",
        reason: "our egress",
        description: "",
        ips: ["203.0.113.4"],
        cidrs: [],
        filter: "",
        expressions: [],
        enabled: true,
        created_at: "2026-09-01T00:00:00Z",
        updated_at: "2026-09-01T00:00:00Z",
      },
      {
        id: 2,
        name: "monitoring",
        kind: "expression",
        reason: "uptime checks",
        description: "",
        ips: [],
        cidrs: [],
        filter: "",
        expressions: ["evt.Parsed.http_user_agent contains 'uptime'"],
        enabled: true,
        created_at: "2026-09-01T00:00:00Z",
        updated_at: "2026-09-01T00:00:00Z",
      },
    ]);
    render(<SecurityView />);
    await user.click(await screen.findByRole("tab", { name: /whitelists/i }));
    const box = await screen.findByRole("searchbox", { name: "Search whitelists" });

    await user.type(box, "uptime");

    expect(screen.getByText("monitoring")).toBeInTheDocument();
    expect(screen.queryByText("office")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 6: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/components/security/security-view.test.tsx`
Expected: FAIL — `Unable to find role="searchbox"`.

- [ ] **Step 7: Wire the whitelists tab**

In `frontend/src/components/security/security-view.tsx` add the imports:

```tsx
import { SearchInput } from "@/components/ui/search-input";
import { filterBySearch } from "@/lib/search";
```

Add `useMemo` to the `react` import, then add beside the other whitelist state:

```tsx
const [wlQuery, setWlQuery] = useState("");

// Name and expressions: an expression whitelist's name rarely says what it
// actually matches, so the rule text has to be searchable.
const visibleWhitelists = useMemo(
  () => filterBySearch(whitelists, wlQuery, (w) => [w.name, ...w.expressions]),
  [whitelists, wlQuery],
);
```

Replace the whitelists panel's toolbar:

```tsx
          <div className="flex justify-end">
            <Button size="sm" onClick={() => setWlDialog({ row: null })}>
              <Plus /> Add whitelist
            </Button>
          </div>
```

with:

```tsx
          <div className="flex flex-wrap items-center justify-between gap-2">
            <SearchInput
              value={wlQuery}
              onValueChange={setWlQuery}
              label="Search whitelists"
              placeholder="Name or expression"
            />
            <Button size="sm" onClick={() => setWlDialog({ row: null })}>
              <Plus /> Add whitelist
            </Button>
          </div>
```

and replace `rows={whitelists}` on `<WhitelistsTable>` with:

```tsx
            rows={visibleWhitelists}
            query={wlQuery}
            onClearSearch={() => setWlQuery("")}
```

- [ ] **Step 8: Run the security tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/security`
Expected: PASS, including `security-view.health.test.tsx`.

- [ ] **Step 9: Typecheck and lint**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/components/security/whitelists-table.tsx frontend/src/components/security/whitelists-table.test.tsx frontend/src/components/security/security-view.tsx frontend/src/components/security/security-view.test.tsx
git commit -m "feat(security): search the whitelists tab

Expressions are searchable alongside the name: an expression whitelist's name
rarely says what it actually matches.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Backend `q` for Decisions and Alerts

The two paginated lists. A client-side filter here would search only the page
on screen and report "no matches" while matches sat on page 3 — silent, and
worse than no search at all. The backend already fetches everything from LAPI
and slices it in memory, so filtering before that slice costs a few lines.

**Files:**
- Modify: `backend/app/services/crowdsec/filtering.py`
- Modify: `backend/app/api/routes/crowdsec.py`
- Modify: `backend/openapi.json` (regenerated)
- Modify: `frontend/src/lib/api/generated/schema.ts` (regenerated)
- Modify: `frontend/src/lib/api/resources/crowdsec.ts`
- Test: `backend/tests/test_crowdsec.py` (append)

**Interfaces:**
- Consumes: `paginate`, `Decision`, `Alert` from the existing modules.
- Produces:
  - `normalise_query(q: str | None) -> str` — trimmed, lower-cased; `""` means no filter.
  - `matches_decision(decision: Decision, needle: str) -> bool` — `needle` must already be lower-cased.
  - `matches_alert(alert: Alert, needle: str) -> bool` — same contract.
  - `GET /api/v1/crowdsec/decisions?q=` and `…/alerts?q=`, filtering **before** `paginate` so `total` is the filtered count.
  - Frontend `ListParams` gains `q?: string`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_crowdsec.py`:

```python
async def test_decisions_q_filters_before_pagination(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    # The trap this exists for: filtering the *page* instead of the whole set
    # tells an operator "no matches" while the match sits on page 3.
    decisions = [
        {
            "origin": "megoopm", "type": "ban", "scope": "Ip", "value": f"10.0.0.{i}",
            "duration": "1h", "scenario": "crowdsecurity/http-probing",
        }
        for i in range(1, 6)
    ]
    decisions.append(
        {
            "origin": "megoopm", "type": "ban", "scope": "Ip", "value": "203.0.113.7",
            "duration": "1h", "scenario": "crowdsecurity/ssh-bf",
        }
    )
    override_crowdsec(lambda r: httpx.Response(200, json=decisions))
    hdr = {"Authorization": f"Bearer {admin_token}"}

    # 203.0.113.7 is the sixth record — page 2 with page_size=5, so a filter
    # applied after pagination would find nothing on page 1.
    resp = await db_client.get(
        "/api/v1/crowdsec/decisions?q=203.0.113.7&page=1&page_size=5", headers=hdr
    )
    assert resp.status_code == 200, resp.text
    assert [d["value"] for d in resp.json()["items"]] == ["203.0.113.7"]


async def test_decisions_q_total_is_the_filtered_count(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    # `total` drives the pager. Returning the unfiltered count offers pages
    # that no longer exist, which reads as data loss.
    decisions = [
        {"origin": "megoopm", "type": "ban", "scope": "Ip", "value": "10.0.0.1", "duration": "1h"},
        {"origin": "megoopm", "type": "ban", "scope": "Ip", "value": "10.0.0.2", "duration": "1h"},
        {"origin": "megoopm", "type": "ban", "scope": "Ip", "value": "203.0.113.7", "duration": "1h"},
    ]
    override_crowdsec(lambda r: httpx.Response(200, json=decisions))
    hdr = {"Authorization": f"Bearer {admin_token}"}

    resp = await db_client.get("/api/v1/crowdsec/decisions?q=203.0", headers=hdr)
    assert resp.json()["total"] == 1


async def test_decisions_q_matches_scenario_case_insensitively(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    decisions = [
        {
            "origin": "megoopm", "type": "ban", "scope": "Ip", "value": "10.0.0.1",
            "duration": "1h", "scenario": "crowdsecurity/SSH-bf",
        },
        {
            "origin": "megoopm", "type": "ban", "scope": "Ip", "value": "10.0.0.2",
            "duration": "1h", "scenario": "crowdsecurity/http-probing",
        },
    ]
    override_crowdsec(lambda r: httpx.Response(200, json=decisions))
    hdr = {"Authorization": f"Bearer {admin_token}"}

    resp = await db_client.get("/api/v1/crowdsec/decisions?q=ssh-BF", headers=hdr)
    assert [d["value"] for d in resp.json()["items"]] == ["10.0.0.1"]


async def test_blank_q_is_not_a_filter(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    # A box the operator selected and hit space in must not empty the table.
    decisions = [
        {"origin": "megoopm", "type": "ban", "scope": "Ip", "value": "10.0.0.1", "duration": "1h"},
    ]
    override_crowdsec(lambda r: httpx.Response(200, json=decisions))
    hdr = {"Authorization": f"Bearer {admin_token}"}

    resp = await db_client.get("/api/v1/crowdsec/decisions?q=%20%20", headers=hdr)
    assert resp.json()["total"] == 1


async def test_alerts_q_matches_source_ip_and_scenario(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/watchers/login":
            return httpx.Response(200, json={"token": "jwt"})
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1, "scenario": "crowdsecurity/ssh-bf", "decisions": None,
                    "source": {"value": "203.0.113.7", "ip": "203.0.113.7"},
                },
                {
                    "id": 2, "scenario": "crowdsecurity/http-probing", "decisions": None,
                    "source": {"value": "198.51.100.2", "ip": "198.51.100.2"},
                },
            ],
        )

    override_crowdsec(handler)
    hdr = {"Authorization": f"Bearer {admin_token}"}

    by_ip = await db_client.get("/api/v1/crowdsec/alerts?q=203.0.113.7", headers=hdr)
    assert by_ip.status_code == 200, by_ip.text
    assert by_ip.json()["total"] == 1
    assert by_ip.json()["items"][0]["id"] == 1

    by_scenario = await db_client.get("/api/v1/crowdsec/alerts?q=probing", headers=hdr)
    assert by_scenario.json()["total"] == 1
    assert by_scenario.json()["items"][0]["id"] == 2


async def test_alerts_q_survives_an_alert_with_no_source(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    # AppSec detections arrive with no source block; matching must not blow up.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/watchers/login":
            return httpx.Response(200, json={"token": "jwt"})
        return httpx.Response(
            200,
            json=[{"id": 1, "scenario": "crowdsecurity/vpatch-env-access", "decisions": None}],
        )

    override_crowdsec(handler)
    hdr = {"Authorization": f"Bearer {admin_token}"}

    resp = await db_client.get("/api/v1/crowdsec/alerts?q=vpatch", headers=hdr)
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Start the throwaway Linux test stack (the app imports `fcntl`, so pytest
cannot run natively on Windows):

```bash
export MSYS_NO_PATHCONV=1
docker network create megoopm-testnet
docker run -d --name megoopm-testdb --network megoopm-testnet \
  -e POSTGRES_USER=megoopm -e POSTGRES_PASSWORD=megoopm -e POSTGRES_DB=megoopm postgres:16-alpine
docker run -d --name megoopm-test --user root --network megoopm-testnet \
  -v "C:/Projects/megoopm/backend:/src" -w /src \
  -e CELERY_TASK_ALWAYS_EAGER=true -e CELERY_RESULT_BACKEND=cache+memory:// \
  -e DATABASE_URL="postgresql+asyncpg://megoopm:megoopm@megoopm-testdb:5432/megoopm" \
  --entrypoint sleep megoopm-backend infinity
docker exec megoopm-test pip install -q "pytest>=8.2" "pytest-asyncio>=0.23" "aiosqlite>=0.20" "ruff>=0.6"
```

Then run (no `-q` — `pyproject.toml` already sets it, and `-qq` swallows the
summary line):

```bash
docker exec megoopm-test python -m pytest tests/test_crowdsec.py -p no:cacheprovider -p no:warnings
```

Expected: the six new tests FAIL — `q` is an unknown parameter, so it is
ignored and every record comes back.

- [ ] **Step 3: Add the matchers**

In `backend/app/services/crowdsec/filtering.py`, add above `paginate`:

```python
def normalise_query(q: str | None) -> str:
    """Trim and lower-case a search query; ``""`` means "no filter".

    A box the operator selected and hit space in is not a filter that matches
    nothing — it is no filter at all.
    """
    return (q or "").strip().lower()


def _contains(needle: str, *fields: str | None) -> bool:
    """True if ``needle`` (already lower-cased) is a substring of any field."""
    return any(field and needle in field.lower() for field in fields)


def matches_decision(decision: Decision, needle: str) -> bool:
    """Match a decision on the banned value or the scenario that fired it."""
    return _contains(needle, decision.value, decision.scenario)


def matches_alert(alert: Alert, needle: str) -> bool:
    """Match an alert on its source IP or the scenario that fired it.

    AppSec detections arrive with no ``source`` block at all, so the attribute
    reads are guarded rather than assumed.
    """
    source = alert.source
    return _contains(
        needle,
        source.value if source else None,
        source.ip if source else None,
        alert.scenario,
    )
```

and extend `__all__`:

```python
__all__ = [
    "ALERT_FETCH_CAP",
    "COMMUNITY_ORIGINS",
    "is_community_alert",
    "is_community_decision",
    "is_community_origin",
    "matches_alert",
    "matches_decision",
    "normalise_query",
    "paginate",
]
```

- [ ] **Step 4: Add `q` to both routes**

In `backend/app/api/routes/crowdsec.py`, extend the filtering import:

```python
from app.services.crowdsec.filtering import (
    ALERT_FETCH_CAP,
    is_community_alert,
    is_community_decision,
    matches_alert,
    matches_decision,
    normalise_query,
    paginate,
)
```

Add the argument type beside `CommunityArg`:

```python
QueryArg = Annotated[
    str | None,
    Query(description="Case-insensitive substring filter, applied before pagination"),
]
```

In `list_decisions`, add the parameter and the filter:

```python
    include_community: CommunityArg = False,
    q: QueryArg = None,
) -> DecisionList:
    """List active decisions, paginated. Hides community origins by default."""
    try:
        items = await client.list_decisions()
    except CrowdSecError as exc:
        raise _handle(exc) from exc
    if not include_community:
        items = [d for d in items if not is_community_decision(d)]
    # Before paginate, deliberately: filtering the page instead of the set
    # would report "no matches" while the match sat on page 3, and `total`
    # would then offer pages that no longer exist.
    needle = normalise_query(q)
    if needle:
        items = [d for d in items if matches_decision(d, needle)]
    page_items, total = paginate(items, page=page, page_size=page_size)
    return DecisionList(items=page_items, total=total, page=page, page_size=page_size)
```

In `list_alerts`, the same:

```python
    include_community: CommunityArg = False,
    q: QueryArg = None,
) -> AlertList:
```

```python
    if not include_community:
        items = [a for a in items if not is_community_alert(a)]
    needle = normalise_query(q)
    if needle:
        items = [a for a in items if matches_alert(a, needle)]
    page_items, total = paginate(items, page=page, page_size=page_size)
    return AlertList(items=page_items, total=total, page=page, page_size=page_size)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_crowdsec.py -p no:cacheprovider -p no:warnings
```

Expected: PASS, all of `test_crowdsec.py`.

- [ ] **Step 6: Regenerate the committed OpenAPI document and the TS types**

Changing the route signature breaks
`tests/test_openapi.py::test_committed_openapi_is_in_sync`:

```bash
docker exec megoopm-test python -m scripts.export_openapi
docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings
```

Expected: the full suite passes (baseline with Postgres reachable: 514 passed,
41 skipped — plus the six new tests).

Then refresh the frontend types:

```bash
cd frontend && npm run gen:api
```

- [ ] **Step 7: Send `q` from the API client**

In `frontend/src/lib/api/resources/crowdsec.ts`, extend `ListParams`:

```ts
export interface ListParams {
  page?: number;
  pageSize?: number;
  includeCommunity?: boolean;
  /** Case-insensitive substring filter, applied server-side before paging. */
  q?: string;
}
```

and widen `listQuery` to carry it:

```ts
function listQuery(params?: ListParams): Record<string, string | number | boolean> {
  const query: Record<string, string | number | boolean> = {};
  if (params?.page != null) query.page = params.page;
  if (params?.pageSize != null) query.page_size = params.pageSize;
  if (params?.includeCommunity != null) query.include_community = params.includeCommunity;
  // Only when non-blank: an empty `q` is no filter, and sending it would make
  // every default page request carry a meaningless parameter.
  if (params?.q?.trim()) query.q = params.q.trim();
  return query;
}
```

- [ ] **Step 8: Typecheck, lint and tear down the test stack**

```bash
cd frontend && npm run typecheck && npm run lint && npm test
```
Expected: no errors; the whole frontend suite passes.

```bash
docker exec megoopm-test ruff check app tests
docker rm -f megoopm-test megoopm-testdb && docker network rm megoopm-testnet
```
Expected: ruff reports no issues in the files this task touched.

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/crowdsec/filtering.py backend/app/api/routes/crowdsec.py backend/tests/test_crowdsec.py backend/openapi.json frontend/src/lib/api/generated/schema.ts frontend/src/lib/api/resources/crowdsec.ts
git commit -m "feat(crowdsec): filter decisions and alerts server-side

A client-side filter on a paginated list searches only the page on screen and
reports 'no matches' while matches sit on page 3 — silent, and worse than no
search. The route already fetches everything from LAPI and slices it in
memory, so q filters before paginate and total is the filtered count: return
the unfiltered one and the pager offers pages that no longer exist.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: The debounce hook

Only the two server-backed tabs need this — a client-side filter is
synchronous and instant, so debouncing it would only add lag.

**Files:**
- Create: `frontend/src/lib/use-debounced-value.ts`
- Test: `frontend/src/lib/use-debounced-value.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `useDebouncedValue<T>(value: T, delayMs: number): T` — returns the previous value until `value` has held still for `delayMs`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/use-debounced-value.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

import { useDebouncedValue } from "@/lib/use-debounced-value";

afterEach(() => vi.useRealTimers());

describe("useDebouncedValue", () => {
  it("returns the initial value immediately", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useDebouncedValue("a", 300));
    expect(result.current).toBe("a");
  });

  it("holds the old value until the delay elapses", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ v }) => useDebouncedValue(v, 300), {
      initialProps: { v: "a" },
    });

    rerender({ v: "ab" });
    expect(result.current).toBe("a");

    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(result.current).toBe("ab");
  });

  it("restarts the clock on every keystroke", () => {
    // The point of debouncing: five characters typed quickly cost one request,
    // not five.
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ v }) => useDebouncedValue(v, 300), {
      initialProps: { v: "a" },
    });

    rerender({ v: "ab" });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    rerender({ v: "abc" });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(result.current).toBe("a");

    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current).toBe("abc");
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npx vitest run src/lib/use-debounced-value.test.ts`
Expected: FAIL — `Failed to resolve import "@/lib/use-debounced-value"`.

- [ ] **Step 3: Write the implementation**

Create `frontend/src/lib/use-debounced-value.ts`:

```ts
"use client";

import { useEffect, useState } from "react";

/**
 * Return `value` only once it has held still for `delayMs`.
 *
 * For search boxes backed by a request: five characters typed quickly should
 * cost one round trip, not five. Client-side filters do not need this — they
 * are synchronous, and debouncing one only adds lag.
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [settled, setSettled] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return settled;
}
```

If `react-hooks/set-state-in-effect` flags the `setSettled` call, add
`// eslint-disable-next-line react-hooks/set-state-in-effect -- debouncing is
a timer settling into state, which is exactly what the rule cannot express`
above it.

- [ ] **Step 4: Run them to verify they pass**

Run: `cd frontend && npx vitest run src/lib/use-debounced-value.test.ts`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/use-debounced-value.ts frontend/src/lib/use-debounced-value.test.ts
git commit -m "feat(search): debounce hook for the request-backed search boxes

Five characters typed quickly should cost one round trip, not five.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Security → Decisions and Alerts

The last two lists. Each search box resets its own table to page 1 the moment
the operator types — before the debounce fires — because filtering while on
page 4 otherwise lands past the end of a shorter result set and shows nothing.

**Files:**
- Modify: `frontend/src/components/security/security-view.tsx`
- Test: `frontend/src/components/security/security-view.test.tsx` (append)

**Interfaces:**
- Consumes: `useDebouncedValue` from `@/lib/use-debounced-value`; `SearchInput` from `@/components/ui/search-input` (imported by Task 7); `crowdsec.listDecisions` / `crowdsec.listAlerts` with the `q` parameter from Task 8.
- Produces: nothing other views import.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/components/security/security-view.test.tsx`. Add `act`
to its `@testing-library/react` import, and give each new `describe` the same
`beforeEach`/`afterEach` block quoted in Task 7, Step 5 — the `listDecisions`
and `listAlerts` spies inside these tests override it.

```tsx
describe("SecurityView decision search", () => {
  it("sends the query to the server and resets to page 1", async () => {
    // Server-side because a client-side filter here would search only the
    // visible page. Page 1 because filtering while on page 4 otherwise lands
    // past the end of a shorter result set.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const listDecisions = vi
      .spyOn(crowdsec, "listDecisions")
      .mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 });

    render(<SecurityView />);
    await user.click(await screen.findByRole("tab", { name: /active decisions/i }));
    const box = await screen.findByRole("searchbox", { name: "Search decisions" });

    await user.type(box, "203.0");
    await act(async () => {
      vi.advanceTimersByTime(400);
    });

    expect(listDecisions.mock.calls.at(-1)?.[0]).toMatchObject({ q: "203.0", page: 1 });
    vi.useRealTimers();
  });

  it("says a search is hiding the decisions, not that there are none", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    vi.spyOn(crowdsec, "listDecisions").mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 50,
    });

    render(<SecurityView />);
    await user.click(await screen.findByRole("tab", { name: /active decisions/i }));
    await user.type(await screen.findByRole("searchbox", { name: "Search decisions" }), "203.0");
    await act(async () => {
      vi.advanceTimersByTime(400);
    });

    expect(await screen.findByText(/no decisions match/i)).toBeInTheDocument();
    expect(screen.queryByText(/no active decisions/i)).not.toBeInTheDocument();
    vi.useRealTimers();
  });
});

describe("SecurityView alert search", () => {
  it("sends the query to the server and resets to page 1", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const listAlerts = vi
      .spyOn(crowdsec, "listAlerts")
      .mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 });

    render(<SecurityView />);
    await user.click(await screen.findByRole("tab", { name: /recent alerts/i }));
    await user.type(await screen.findByRole("searchbox", { name: "Search alerts" }), "ssh");
    await act(async () => {
      vi.advanceTimersByTime(400);
    });

    expect(listAlerts.mock.calls.at(-1)?.[0]).toMatchObject({ q: "ssh", page: 1 });
    vi.useRealTimers();
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npx vitest run src/components/security/security-view.test.tsx`
Expected: FAIL — `Unable to find role="searchbox"` named "Search decisions".

- [ ] **Step 3: Add the search state**

In `frontend/src/components/security/security-view.tsx` add the import:

```tsx
import { useDebouncedValue } from "@/lib/use-debounced-value";
```

Add the constant beside `COMMUNITY_KEY`:

```tsx
/** Long enough that a typed IP costs one request, short enough to feel live. */
const SEARCH_DEBOUNCE_MS = 300;
```

Add the state beside the decisions pagination state:

```tsx
const [decQuery, setDecQuery] = useState("");
const decSearch = useDebouncedValue(decQuery, SEARCH_DEBOUNCE_MS);
```

and beside the alerts pagination state:

```tsx
const [alertQuery, setAlertQuery] = useState("");
const alertSearch = useDebouncedValue(alertQuery, SEARCH_DEBOUNCE_MS);
```

Add the two change handlers next to `setCommunity`. They reset the page in the
handler rather than in an effect — an effect that calls `setDecPage` would trip
`react-hooks/set-state-in-effect` and race the fetch:

```tsx
const changeDecQuery = useCallback((next: string) => {
  setDecQuery(next);
  // Immediately, not after the debounce: filtering while on page 4 otherwise
  // lands past the end of a shorter result set and shows an empty table.
  setDecPage(1);
}, []);

const changeAlertQuery = useCallback((next: string) => {
  setAlertQuery(next);
  setAlertPage(1);
}, []);
```

- [ ] **Step 4: Send the query with each fetch**

In the decisions effect, pass `q` and add `decSearch` to the dependency array:

```tsx
        const list = await crowdsec.listDecisions({
          page: decPage,
          pageSize: decPageSize,
          includeCommunity,
          q: decSearch,
        });
```

```tsx
  }, [decPage, decPageSize, includeCommunity, decSearch, refreshTick]);
```

In the alerts effect, the same:

```tsx
        const list = await crowdsec.listAlerts({
          page: alertPage,
          pageSize: alertPageSize,
          includeCommunity,
          q: alertSearch,
        });
```

```tsx
  }, [alertPage, alertPageSize, includeCommunity, alertSearch, refreshTick]);
```

- [ ] **Step 5: Put a box above each table**

In the decisions panel, insert immediately above its `<div className="rounded-xl border">`:

```tsx
              <div className="flex flex-wrap items-center justify-between gap-2">
                <SearchInput
                  value={decQuery}
                  onValueChange={changeDecQuery}
                  label="Search decisions"
                  placeholder="IP, range or scenario"
                />
              </div>
```

and in the alerts panel, above its `<div className="rounded-xl border">`:

```tsx
              <div className="flex flex-wrap items-center justify-between gap-2">
                <SearchInput
                  value={alertQuery}
                  onValueChange={changeAlertQuery}
                  label="Search alerts"
                  placeholder="Source IP or scenario"
                />
              </div>
```

- [ ] **Step 6: Give each table its filtered-empty state**

Replace the decisions empty cell body:

```tsx
                          No active decisions
                          {includeCommunity ? "" : " (community records are hidden)"}. The bouncer
                          isn’t enforcing any matching bans right now.
```

with:

```tsx
                          {decQuery.trim() ? (
                            <>
                              No decisions match “{decQuery.trim()}”.{" "}
                              <Button
                                variant="link"
                                size="sm"
                                className="h-auto p-0 align-baseline"
                                onClick={() => changeDecQuery("")}
                              >
                                Clear search
                              </Button>
                            </>
                          ) : (
                            <>
                              No active decisions
                              {includeCommunity ? "" : " (community records are hidden)"}. The
                              bouncer isn’t enforcing any matching bans right now.
                            </>
                          )}
```

Replace the alerts empty cell body:

```tsx
                          No recent alerts
                          {includeCommunity ? "" : " (community records are hidden)"}.
```

with:

```tsx
                          {alertQuery.trim() ? (
                            <>
                              No alerts match “{alertQuery.trim()}”.{" "}
                              <Button
                                variant="link"
                                size="sm"
                                className="h-auto p-0 align-baseline"
                                onClick={() => changeAlertQuery("")}
                              >
                                Clear search
                              </Button>
                            </>
                          ) : (
                            <>
                              No recent alerts
                              {includeCommunity ? "" : " (community records are hidden)"}.
                            </>
                          )}
```

- [ ] **Step 7: Run the security tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/security`
Expected: PASS, including the Task 7 whitelist tests and the pre-existing
health tests.

- [ ] **Step 8: Run the whole frontend suite, typecheck and lint**

Run: `cd frontend && npm test && npm run typecheck && npm run lint`
Expected: no failures, no type errors, no lint errors.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/security/security-view.tsx frontend/src/components/security/security-view.test.tsx
git commit -m "feat(security): search the decisions and alerts tabs

Each box resets its own table to page 1 the moment the operator types, before
the debounce fires: filtering while on page 4 otherwise lands past the end of
a shorter result set and shows an empty table.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Manual verification

The test suite cannot see layout, and every production bug this project has
hit was found by running the real thing. After Task 10, with the stack up:

- [ ] Open each of the nine client-side pages. The box sits on the same side
      of the toolbar on all of them, and the "New …" button has not moved on
      the five pages that had one there.
- [ ] Type a partial domain on Proxy Hosts. The table narrows as you type,
      with no perceptible lag.
- [ ] Type something that matches nothing. The message names the query and
      offers "Clear search"; clicking it restores the table.
- [ ] On Security → Active decisions with more than one page of records,
      search for a value you know is on the last page. It is found — this is
      the failure a client-side filter would have.
- [ ] Watch the network tab while typing five characters there: one request,
      not five, and `page=1` on it.
- [ ] Narrow the browser to a phone width. The toolbar wraps; the box does not
      overflow the card.
