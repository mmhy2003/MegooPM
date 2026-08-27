import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import {
  NO_CERTIFICATE,
  buildPayload,
  describeError,
  newLocationRow,
  parseDomains,
  stateFromHost,
  validateForm,
  validateLocations,
  type LocationRow,
} from "@/components/proxy-hosts/lib";
import { makeHost } from "@/components/proxy-hosts/test-utils";

function row(patch: Partial<LocationRow>): LocationRow {
  return { ...newLocationRow(), path: "/api/", upstreamId: "2", scheme: "http", ...patch };
}

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

describe("validateLocations", () => {
  it("accepts distinct prefixed paths with pools", () => {
    expect(validateLocations([row({}), row({ path: "/api" })])).toBeNull();
  });

  it.each([
    [row({ path: "api" }), "must start with /"],
    [row({ path: "/" }), "root"],
    [row({ path: "/a b" }), "whitespace"],
    [row({ path: '/a"b' }), "whitespace"],
    [row({ path: "/" + "x".repeat(255) }), "255"],
    [row({ upstreamId: "" }), "Select an upstream pool for /api/"],
  ])("rejects %j", (bad, fragment) => {
    const err = validateLocations([bad]);
    expect(err?.tab).toBe("forwarding");
    expect(err?.message).toContain(fragment);
  });

  it("rejects duplicate paths", () => {
    expect(validateLocations([row({}), row({})])?.message).toContain("Duplicate location path");
  });
});

describe("validateForm", () => {
  it("checks domains, then the root pool, then locations", () => {
    const base = stateFromHost(makeHost());
    expect(validateForm({ ...base, domains: [] })).toEqual({
      message: "Enter at least one domain name.",
      tab: null,
    });
    expect(validateForm({ ...base, rootUpstreamId: "" })).toEqual({
      message: "Select an upstream pool to forward to.",
      tab: "forwarding",
    });
    expect(validateForm({ ...base, locations: [row({ path: "bad" })] })?.tab).toBe("forwarding");
    expect(validateForm(base)).toBeNull();
  });
});

describe("stateFromHost / buildPayload", () => {
  it("round-trips a host with a certificate and locations", () => {
    const host = makeHost({
      certificate_id: 7,
      ssl_forced: true,
      locations: [{ id: 5, path: "/api/", upstream_id: 2, forward_scheme: "https" }],
    });
    const form = stateFromHost(host);
    expect(form.certificateId).toBe("7");
    expect(form.locations).toEqual([
      { key: "loc-5", path: "/api/", upstreamId: "2", scheme: "https" },
    ]);
    expect(buildPayload(form, host)).toMatchObject({
      domain_names: ["app.example.com"],
      upstream_id: 1,
      forward_scheme: "http",
      certificate_id: 7,
      access_list_id: null,
      ssl_forced: true,
      locations: [{ path: "/api/", upstream_id: 2, forward_scheme: "https" }],
    });
  });

  it("sends null for no certificate and trims location paths", () => {
    const form = { ...stateFromHost(null), rootUpstreamId: "1", domains: ["a.com"] };
    form.locations = [row({ path: " /ws " })];
    expect(form.certificateId).toBe(NO_CERTIFICATE);
    const payload = buildPayload(form, null);
    expect(payload.certificate_id).toBeNull();
    expect(payload.locations).toEqual([{ path: "/ws", upstream_id: 2, forward_scheme: "http" }]);
    expect(payload.crowdsec_enabled).toBe(false);
  });

  it("passes CrowdSec flags through from the existing host", () => {
    const host = makeHost({ crowdsec_enabled: true, crowdsec_appsec_enabled: true });
    expect(buildPayload(stateFromHost(host), host)).toMatchObject({
      crowdsec_enabled: true,
      crowdsec_appsec_enabled: true,
    });
  });
});
