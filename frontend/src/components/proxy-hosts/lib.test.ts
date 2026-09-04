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
        {
          loc: ["body", "domain_names", 0],
          msg: "invalid domain name: 'bad_'",
          type: "value_error",
        },
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
      {
        key: "loc-5",
        path: "/api/",
        targetMode: "pool",
        upstreamId: "2",
        forwardHost: "",
        forwardPort: "",
        scheme: "https",
        customPageId: "",
      },
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
    expect(payload.locations).toEqual([
      {
        path: "/ws",
        target: "pool",
        upstream_id: 2,
        forward_host: null,
        forward_port: null,
        forward_scheme: "http",
        custom_page_id: null,
      },
    ]);
    expect(payload.crowdsec_enabled).toBe(false);
  });

  it("passes CrowdSec flags through from the existing host", () => {
    const host = makeHost({ crowdsec_enabled: true, crowdsec_appsec_enabled: true });
    expect(buildPayload(stateFromHost(host), host)).toMatchObject({
      crowdsec_enabled: true,
      crowdsec_appsec_enabled: true,
    });
  });

  it("lets the form change crowdsec_enabled while AppSec stays a pass-through", () => {
    const host = makeHost({ crowdsec_enabled: true, crowdsec_appsec_enabled: true });
    const form = stateFromHost(host);
    expect(form.toggles.crowdsec_enabled).toBe(true);
    form.toggles.crowdsec_enabled = false;
    expect(buildPayload(form, host)).toMatchObject({
      crowdsec_enabled: false,
      crowdsec_appsec_enabled: true,
    });
  });
});

describe("root forward target", () => {
  it("defaults a new host to the pool target", () => {
    expect(stateFromHost(null).rootTargetMode).toBe("pool");
  });

  it("opens on the mode the host actually uses", () => {
    const state = stateFromHost(
      makeHost({ upstream_id: null, forward_host: "10.0.0.1", forward_port: 8080 }),
    );
    expect(state.rootTargetMode).toBe("host");
    expect(state.rootForwardHost).toBe("10.0.0.1");
    expect(state.rootForwardPort).toBe("8080");
  });

  it("sends a pool target with the host side nulled", () => {
    const out = buildPayload(
      { ...stateFromHost(null), domains: ["a.example.com"], rootUpstreamId: "2" },
      null,
    );
    expect(out.upstream_id).toBe(2);
    expect(out.forward_host).toBeNull();
    expect(out.forward_port).toBeNull();
  });

  it("sends a host target with the pool side nulled", () => {
    const out = buildPayload(
      {
        ...stateFromHost(null),
        domains: ["a.example.com"],
        rootTargetMode: "host",
        rootForwardHost: "10.0.0.1",
        rootForwardPort: "8080",
      },
      null,
    );
    expect(out.upstream_id).toBeNull();
    expect(out.forward_host).toBe("10.0.0.1");
    expect(out.forward_port).toBe(8080);
  });

  it("requires a host and a valid port in host mode", () => {
    const base = {
      ...stateFromHost(null),
      domains: ["a.example.com"],
      rootTargetMode: "host" as const,
    };
    expect(validateForm(base)?.message).toMatch(/forward host/i);
    expect(
      validateForm({ ...base, rootForwardHost: "10.0.0.1", rootForwardPort: "70000" })?.message,
    ).toMatch(/65535/);
  });

  it("still requires a pool in pool mode", () => {
    const form = { ...stateFromHost(null), domains: ["a.example.com"] };
    expect(validateForm(form)?.message).toMatch(/pool/i);
  });
});

describe("location forward target", () => {
  it("defaults a new row to the pool target", () => {
    expect(newLocationRow().targetMode).toBe("pool");
  });

  it("validates each row by its own mode", () => {
    const rows = [
      { ...newLocationRow(), path: "/api", targetMode: "host" as const },
      { ...newLocationRow(), path: "/img", upstreamId: "2" },
    ];
    expect(validateLocations(rows)?.message).toMatch(/forward host for \/api/i);
  });

  it("accepts a host-targeted row with a host and port", () => {
    const rows = [
      {
        ...newLocationRow(),
        path: "/api",
        targetMode: "host" as const,
        forwardHost: "10.0.0.9",
        forwardPort: "9000",
      },
    ];
    expect(validateLocations(rows)).toBeNull();
  });

  it("sends one target per row", () => {
    const rows = [
      { ...newLocationRow(), path: "/api", upstreamId: "2" },
      {
        ...newLocationRow(),
        path: "/img",
        targetMode: "host" as const,
        forwardHost: "10.0.0.9",
        forwardPort: "9000",
      },
    ];
    const out = buildPayload(
      { ...stateFromHost(null), domains: ["a.example.com"], rootUpstreamId: "1", locations: rows },
      null,
    );
    expect(out.locations?.[0]).toMatchObject({ upstream_id: 2, forward_host: null });
    expect(out.locations?.[1]).toMatchObject({
      upstream_id: null,
      forward_host: "10.0.0.9",
      forward_port: 9000,
    });
  });
});

describe("locations nginx answers itself", () => {
  it("reads a stored target rather than inferring it from the columns", () => {
    const state = stateFromHost(
      makeHost({
        locations: [
          { id: 1, path: "/legacy/", target: "default_site", forward_scheme: "http" },
          {
            id: 2,
            path: "/maint/",
            target: "custom_page",
            custom_page_id: 4,
            forward_scheme: "http",
          },
        ],
      }) as never,
    );
    expect(state.locations.map((l) => l.targetMode)).toEqual(["default_site", "custom_page"]);
    expect(state.locations[1].customPageId).toBe("4");
  });

  it("sends no backend for an answered location", () => {
    const payload = buildPayload(
      {
        ...stateFromHost(makeHost()),
        locations: [
          { ...newLocationRow(), path: "/legacy/", targetMode: "default_site" as const },
          {
            ...newLocationRow(),
            path: "/maint/",
            targetMode: "custom_page" as const,
            customPageId: "4",
          },
        ],
      },
      null,
    );
    expect(payload.locations).toEqual([
      {
        path: "/legacy/",
        target: "default_site",
        upstream_id: null,
        forward_host: null,
        forward_port: null,
        forward_scheme: "http",
        custom_page_id: null,
      },
      {
        path: "/maint/",
        target: "custom_page",
        upstream_id: null,
        forward_host: null,
        forward_port: null,
        forward_scheme: "http",
        custom_page_id: 4,
      },
    ]);
  });

  it("requires a page for a custom-page location and nothing for the default site", () => {
    expect(
      validateLocations([
        { ...newLocationRow(), path: "/maint/", targetMode: "custom_page" as const },
      ]),
    ).toEqual({ message: "Select a page for /maint/.", tab: "forwarding" });
    expect(
      validateLocations([
        { ...newLocationRow(), path: "/legacy/", targetMode: "default_site" as const },
      ]),
    ).toBeNull();
  });
});
