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
