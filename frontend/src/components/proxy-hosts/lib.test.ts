import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { describeError, parseDomains } from "@/components/proxy-hosts/lib";

describe("parseDomains", () => {
  it("splits on commas and whitespace, lower-casing and de-duplicating", () => {
    expect(parseDomains("Example.com, www.example.com  api.example.com")).toEqual([
      "example.com",
      "www.example.com",
      "api.example.com",
    ]);
  });

  it("drops duplicates regardless of case and surrounding space", () => {
    expect(parseDomains("a.com, A.com\n a.com ")).toEqual(["a.com"]);
  });

  it("returns an empty list for blank input", () => {
    expect(parseDomains("   \n  ")).toEqual([]);
  });
});

describe("describeError", () => {
  it("surfaces a plain FastAPI detail string", () => {
    const err = new ApiError(409, "conflict", { detail: "Upstream is still referenced" });
    expect(describeError(err)).toEqual({
      message: "Upstream is still referenced",
      fieldErrors: {},
    });
  });

  it("maps a 422 validation list to per-field messages", () => {
    const err = new ApiError(422, "unprocessable", {
      detail: [
        { loc: ["body", "domain_names", 0], msg: "invalid domain name: 'bad_'", type: "value_error" },
        { loc: ["body", "upstream_id"], msg: "field required", type: "missing" },
      ],
    });
    const described = describeError(err);
    expect(described.fieldErrors).toEqual({
      domain_names: "invalid domain name: 'bad_'",
      upstream_id: "field required",
    });
    expect(described.message).toBe("invalid domain name: 'bad_'");
  });

  it("falls back for non-ApiError throwables", () => {
    expect(describeError(new Error("boom")).message).toBe("boom");
    expect(describeError("weird").message).toMatch(/went wrong/i);
  });
});
