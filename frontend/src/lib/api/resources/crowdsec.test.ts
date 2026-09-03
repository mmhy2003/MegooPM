import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api/client";
import { crowdsec } from "@/lib/api/resources/crowdsec";

/**
 * These pin the query-param wiring for the paginated CrowdSec lists (MEG-44):
 * camelCase call args must map to the backend's snake_case `page` /
 * `page_size` / `include_community`, and the mutations must hit the right URLs.
 */
describe("crowdsec resource", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists decisions with pagination + community params mapped to snake_case", async () => {
    const get = vi
      .spyOn(api, "get")
      .mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 });

    await crowdsec.listDecisions({ page: 3, pageSize: 25, includeCommunity: true });

    expect(get).toHaveBeenCalledWith("/api/v1/crowdsec/decisions", {
      query: { page: 3, page_size: 25, include_community: true },
    });
  });

  it("lists alerts with the same param mapping", async () => {
    const get = vi
      .spyOn(api, "get")
      .mockResolvedValue({ items: [], total: 0, page: 2, page_size: 10 });

    await crowdsec.listAlerts({ page: 2, pageSize: 10, includeCommunity: false });

    expect(get).toHaveBeenCalledWith("/api/v1/crowdsec/alerts", {
      query: { page: 2, page_size: 10, include_community: false },
    });
  });

  it("omits unset params so the backend defaults apply", async () => {
    const get = vi
      .spyOn(api, "get")
      .mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 });

    await crowdsec.listDecisions();

    expect(get).toHaveBeenCalledWith("/api/v1/crowdsec/decisions", { query: {} });
  });

  it("posts a manual decision to the decisions collection", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({});
    const body = {
      value: "203.0.113.4",
      scope: "Ip",
      type: "ban",
      duration: "4h",
      reason: null,
    } as const;

    await crowdsec.addDecision(body);

    expect(post).toHaveBeenCalledWith("/api/v1/crowdsec/decisions", body);
  });

  it("deletes a decision by id", async () => {
    const del = vi.spyOn(api, "delete").mockResolvedValue({ deleted: 1 });

    await crowdsec.deleteDecision(42);

    expect(del).toHaveBeenCalledWith("/api/v1/crowdsec/decisions/42");
  });
});
