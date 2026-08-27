import type { ProxyHost } from "@/lib/api";

/** A fully-populated ProxyHost row for tests; override any field via `patch`. */
export function makeHost(patch: Partial<ProxyHost> = {}): ProxyHost {
  return {
    id: 1,
    domain_names: ["app.example.com"],
    upstream_id: 1,
    forward_scheme: "http",
    certificate_id: null,
    access_list_id: null,
    ssl_forced: false,
    http2_support: false,
    hsts_enabled: false,
    hsts_subdomains: false,
    caching_enabled: false,
    block_exploits: false,
    allow_websocket_upgrade: false,
    crowdsec_enabled: false,
    crowdsec_appsec_enabled: false,
    advanced_config: "",
    enabled: true,
    locations: [],
    created_at: "2026-08-27T00:00:00Z",
    updated_at: "2026-08-27T00:00:00Z",
    ...patch,
  };
}
