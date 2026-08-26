import { afterEach, describe, expect, it, vi } from "vitest";

import { api, apiFetch, setAuthTokenProvider } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";

function mockResponse(
  body: unknown,
  init: { status?: number; contentType?: string } = {},
) {
  const { status = 200, contentType = "application/json" } = init;
  return new Response(
    typeof body === "string" ? body : JSON.stringify(body),
    { status, headers: { "content-type": contentType } },
  );
}

describe("apiFetch", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    setAuthTokenProvider(() => null);
  });

  it("builds an absolute URL from the base and appends query params", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockResponse({ ok: true }));

    await apiFetch("/api/proxy-hosts", { query: { page: 2, q: null, enabled: true } });

    const url = new URL(fetchMock.mock.calls[0][0] as string);
    expect(url.pathname).toBe("/api/proxy-hosts");
    expect(url.searchParams.get("page")).toBe("2");
    expect(url.searchParams.get("enabled")).toBe("true");
    // null/undefined params are dropped.
    expect(url.searchParams.has("q")).toBe(false);
  });

  it("attaches a bearer token from the provider", async () => {
    setAuthTokenProvider(() => "secret-token");
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockResponse({}));

    await api.get("/api/me");

    const headers = (fetchMock.mock.calls[0][1] as RequestInit).headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer secret-token");
  });

  it("serializes JSON bodies and sets the content type", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockResponse({ id: 1 }));

    await api.post("/api/proxy-hosts", { domain: "example.com" });

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ domain: "example.com" }));
    expect((init.headers as Headers).get("Content-Type")).toBe("application/json");
  });

  it("throws a typed ApiError on non-2xx responses", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      mockResponse({ detail: "Not found" }, { status: 404 }),
    );

    const error = await apiFetch("/api/proxy-hosts/999").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(404);
    expect((error as ApiError).detail).toBe("Not found");
    expect((error as ApiError).isUnauthorized).toBe(false);
  });

  it("returns undefined for 204 responses", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 204 }),
    );

    await expect(api.delete("/api/proxy-hosts/1")).resolves.toBeUndefined();
  });
});
