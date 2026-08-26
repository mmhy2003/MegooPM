import { afterEach, describe, expect, it, vi } from "vitest";

import {
  api,
  apiFetch,
  setAuthTokenProvider,
  setTokenRefresher,
} from "@/lib/api/client";
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
    setTokenRefresher(null);
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

  it("refreshes the token and retries once on a 401", async () => {
    setAuthTokenProvider(() => "stale-token");
    setTokenRefresher(async () => "fresh-token");

    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(mockResponse({ detail: "expired" }, { status: 401 }))
      .mockResolvedValueOnce(mockResponse({ ok: true }));

    await expect(api.get("/api/me")).resolves.toEqual({ ok: true });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const retryHeaders = (fetchMock.mock.calls[1][1] as RequestInit)
      .headers as Headers;
    expect(retryHeaders.get("Authorization")).toBe("Bearer fresh-token");
  });

  it("propagates the 401 when refresh yields no token", async () => {
    setAuthTokenProvider(() => "stale-token");
    setTokenRefresher(async () => null);

    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockResponse({ detail: "expired" }, { status: 401 }));

    const error = await api.get("/api/me").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not refresh when an explicit token is supplied (auth endpoints)", async () => {
    setTokenRefresher(async () => "should-not-be-used");

    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockResponse({ detail: "bad creds" }, { status: 401 }));

    const error = await apiFetch("/api/v1/auth/login", {
      method: "POST",
      body: { email: "a@b.c", password: "x" },
      token: null,
    }).catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
