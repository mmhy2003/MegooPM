import { describe, expect, it } from "vitest";

import { actionTone, describeObject, summarise } from "@/components/audit-log/lib";

describe("summarise", () => {
  it("says nothing when there is nothing to say", () => {
    // An em-dash, not "{}": a row with no detail is common and must not look
    // like a bug.
    expect(summarise({})).toBe("—");
  });

  it("renders a flat field as name and value", () => {
    expect(summarise({ name: "CI deploy" })).toBe("name: CI deploy");
  });

  it("renders a diff as an arrow, and only under `changes`", () => {
    // Handlers always nest a diff under `changes` — meta={"changes": {...}}.
    expect(summarise({ changes: { is_active: [true, false] } })).toBe("is_active: true → false");
  });

  it("does not mistake a two-item list for a diff", () => {
    // A host with two domains is not a change from one to the other, and an
    // audit log that says so is lying about what happened.
    expect(summarise({ domain_names: ["a.example.com", "b.example.com"] })).toBe(
      "domain_names: a.example.com, b.example.com",
    );
  });

  it("keeps several fields in the order they were written", () => {
    expect(summarise({ mode: "megoopm", page_id: 4 })).toBe("mode: megoopm, page_id: 4");
  });

  it("says null rather than dropping the field", () => {
    // "the page was cleared" is a real change; an omitted field reads as no
    // change at all.
    expect(summarise({ page_id: null })).toBe("page_id: none");
  });

  it("summarises a nested object as its shape, not its contents", () => {
    // The dialog shows the whole thing; the cell must stay one line.
    expect(summarise({ backend: { host: "10.0.0.5", port: 80 } })).toBe("backend: {…}");
  });

  it("truncates a summary that would break the row", () => {
    const long = summarise({ note: "x".repeat(300) });

    expect(long.length).toBeLessThanOrEqual(120);
    expect(long.endsWith("…")).toBe(true);
  });
});

describe("describeObject", () => {
  it("reads an object type as words, with its id", () => {
    expect(describeObject("proxy_host", 7)).toBe("Proxy host #7");
  });

  it("leaves off an id the row does not have", () => {
    // Settings rows carry no id; "#null" would be worse than nothing.
    expect(describeObject("instance_settings", null)).toBe("Instance settings");
  });
});

describe("actionTone", () => {
  it("separates the destructive actions from the rest", () => {
    // Colour is the only thing that makes a delete findable while scrolling.
    expect(actionTone("delete")).toBe("destructive");
    expect(actionTone("disable")).toBe("destructive");
    expect(actionTone("create")).toBe("positive");
    expect(actionTone("enable")).toBe("positive");
    expect(actionTone("update")).toBe("neutral");
  });
});
