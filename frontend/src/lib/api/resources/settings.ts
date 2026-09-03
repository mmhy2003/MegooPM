/**
 * Typed client for the instance-settings endpoints.
 *
 * One settings row exists, so no path carries an id — but each settings *group*
 * gets its own PATCH. A single patch over the whole row cannot work: each group
 * has a coherence rule ("redirect needs a URL", "enabled needs a model") that
 * can only be checked against a payload carrying that group's discriminator, so
 * one combined route would force resending every group to change any of them.
 *
 * The LLM API key is never returned by `get` — only `llm_api_key_set`.
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type InstanceSettings = Schemas["InstanceSettingsRead"];
export type DefaultSiteUpdate = Schemas["InstanceSettingsUpdate"];
export type DefaultSiteMode = Schemas["DefaultSiteMode"];
export type CrowdSecBanUpdate = Schemas["CrowdSecBanUpdate"];
export type CrowdSecBanMode = Schemas["CrowdSecBanMode"];
export type LlmSettingsUpdate = Schemas["LlmSettingsUpdate"];
export type LlmTestRequest = Schemas["LlmTestRequest"];
export type LlmTestResult = Schemas["LlmTestResult"];
export type SmtpSettingsUpdate = Schemas["SmtpSettingsUpdate"];
export type SmtpSecurity = Schemas["SmtpSecurity"];
export type MailTestRequest = Schemas["MailTestRequest"];
export type MailTestResult = Schemas["MailTestResult"];
export type CrowdSecHubUpdate = Schemas["CrowdSecHubUpdate"];
export type CrowdSecCapiUpdate = Schemas["CrowdSecCapiUpdate"];
export type HubUpdateFrequency = Schemas["HubUpdateFrequency"];

const BASE = "/api/v1/settings";

export const instanceSettings = {
  get: () => api.get<InstanceSettings>(BASE),
  updateDefaultSite: (body: DefaultSiteUpdate) =>
    api.patch<InstanceSettings>(`${BASE}/default-site`, body),
  updateBanPage: (body: CrowdSecBanUpdate) => api.patch<InstanceSettings>(`${BASE}/ban-page`, body),
  updateLlm: (body: LlmSettingsUpdate) => api.patch<InstanceSettings>(`${BASE}/llm`, body),
  /**
   * Runs a real completion. Overrides win over stored values, so a key can be
   * checked before it is saved, and it works while the feature is switched off.
   * A failed probe comes back as `ok: false` with HTTP 200 — the API call
   * succeeded, the upstream did not.
   */
  testLlm: (body: LlmTestRequest) => api.post<LlmTestResult>(`${BASE}/llm/test`, body),
  updateSmtp: (body: SmtpSettingsUpdate) => api.patch<InstanceSettings>(`${BASE}/smtp`, body),
  /**
   * Sends one real message using the *stored* settings, so save first. A
   * failure comes back as `ok: false` with HTTP 200 — the API call
   * succeeded, the mail server did not.
   */
  testSmtp: (body: MailTestRequest) => api.post<MailTestResult>(`${BASE}/smtp/test`, body),
  /** The hub refresh schedule; takes effect at the next hourly tick. */
  updateCrowdSecHub: (body: CrowdSecHubUpdate) =>
    api.patch<InstanceSettings>(`${BASE}/crowdsec-hub`, body),
  /** Desired blocklist state; 202 and an apply is queued (CrowdSec restarts). */
  updateCrowdSecCapi: (body: CrowdSecCapiUpdate) =>
    api.patch<InstanceSettings>(`${BASE}/crowdsec-capi`, body),
} as const;
