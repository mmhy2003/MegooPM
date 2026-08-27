import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api/client";
import { dnsCredentials, dnsProviders } from "@/lib/api/resources/dns-providers";

describe("dns-providers resources", () => {
  afterEach(() => vi.restoreAllMocks());

  it("targets the catalog and credential endpoints", async () => {
    vi.spyOn(api, "get").mockResolvedValue([] as never);
    vi.spyOn(api, "post").mockResolvedValue({} as never);
    vi.spyOn(api, "patch").mockResolvedValue({} as never);
    vi.spyOn(api, "delete").mockResolvedValue(undefined as never);

    await dnsProviders.catalog();
    await dnsCredentials.list();
    await dnsCredentials.create({ name: "cf", provider: "cloudflare", options: { auth_token: "t" } });
    await dnsCredentials.update(3, { name: "cf2" });
    await dnsCredentials.verify(3, { domain: "example.com" });
    await dnsCredentials.remove(3);

    expect(api.get).toHaveBeenCalledWith("/api/v1/dns-providers");
    expect(api.get).toHaveBeenCalledWith("/api/v1/dns-credentials");
    expect(api.post).toHaveBeenCalledWith("/api/v1/dns-credentials", {
      name: "cf",
      provider: "cloudflare",
      options: { auth_token: "t" },
    });
    expect(api.patch).toHaveBeenCalledWith("/api/v1/dns-credentials/3", { name: "cf2" });
    expect(api.post).toHaveBeenCalledWith("/api/v1/dns-credentials/3/verify", {
      domain: "example.com",
    });
    expect(api.delete).toHaveBeenCalledWith("/api/v1/dns-credentials/3");
  });
});
