import { describe, expect, it } from "vitest";

import { classifyWebAuthnError } from "@/lib/auth/webauthn";

function dom(name: string): Error {
  const e = new Error(name);
  e.name = name;
  return e;
}

describe("classifyWebAuthnError", () => {
  it("treats a dismissed prompt as cancelled, wrapped or not", () => {
    expect(classifyWebAuthnError(dom("NotAllowedError"))).toBe("cancelled");
    expect(
      classifyWebAuthnError({ code: "ERROR_CEREMONY_ABORTED", cause: dom("AbortError") }),
    ).toBe("cancelled");
    expect(
      classifyWebAuthnError({
        code: "ERROR_PASSTHROUGH_SEE_CAUSE_PROPERTY",
        cause: dom("NotAllowedError"),
      }),
    ).toBe("cancelled");
  });

  it("recognises an origin or RP ID mismatch", () => {
    expect(classifyWebAuthnError(dom("SecurityError"))).toBe("origin");
    expect(classifyWebAuthnError({ code: "ERROR_INVALID_DOMAIN" })).toBe("origin");
    expect(classifyWebAuthnError({ code: "ERROR_INVALID_RP_ID" })).toBe("origin");
  });

  it("recognises a browser without WebAuthn", () => {
    expect(classifyWebAuthnError(dom("NotSupportedError"))).toBe("unsupported");
  });

  it("falls through to other", () => {
    expect(classifyWebAuthnError(new Error("boom"))).toBe("other");
    expect(classifyWebAuthnError(null)).toBe("other");
  });
});
