import { describe, expect, it } from "vitest";

import {
  buildOptionsPayload,
  credentialLabel,
  emptyValues,
  fieldLabel,
  missingSecret,
} from "@/components/dns-providers/lib";

const fields = [
  { name: "auth_token", label: "Auth token", help: "", secret: true },
  { name: "auth_username", label: "Auth username", help: "", secret: true },
  { name: "zone_id", label: "Zone id", help: "", secret: false },
];

describe("credentialLabel / fieldLabel", () => {
  it("formats labels", () => {
    expect(credentialLabel({ name: "cf-prod", provider_label: "Cloudflare" })).toBe(
      "cf-prod · Cloudflare",
    );
    expect(fieldLabel("auth_token")).toBe("Auth token");
    expect(fieldLabel("pdns_server_id")).toBe("Pdns server id");
  });
});

describe("emptyValues", () => {
  it("creates a blank value per field", () => {
    expect(emptyValues(fields)).toEqual({ auth_token: "", auth_username: "", zone_id: "" });
  });
});

describe("buildOptionsPayload", () => {
  it("trims values and drops blanks (blank secrets mean 'unchanged' when editing)", () => {
    expect(
      buildOptionsPayload(fields, { auth_token: "  tok ", auth_username: "", zone_id: " " }),
    ).toEqual({ auth_token: "tok" });
  });
});

describe("missingSecret", () => {
  it("is true only when every secret field is blank", () => {
    expect(missingSecret(fields, { auth_token: "", auth_username: " ", zone_id: "z" })).toBe(true);
    expect(missingSecret(fields, { auth_token: "t", auth_username: "", zone_id: "" })).toBe(false);
  });
});
