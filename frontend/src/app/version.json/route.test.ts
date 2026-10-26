// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

import { GET } from "@/app/version.json/route";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("GET /version.json", () => {
  it("reports the build the server is running, never from a cache", async () => {
    vi.stubEnv("NEXT_DEPLOYMENT_ID", "abc123");
    const res = GET();
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(await res.json()).toEqual({ id: "abc123" });
  });

  it("reports no build where none was given one (a dev server)", async () => {
    vi.stubEnv("NEXT_DEPLOYMENT_ID", "");
    expect(await GET().json()).toEqual({ id: null });
  });
});
