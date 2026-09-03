/**
 * Public entrypoint for the MegooPM API client.
 *
 * Feature tickets add resource modules under `src/lib/api/resources/` and
 * re-export them here so callers import from a single, stable path:
 * `import { api, apiFetch } from "@/lib/api"`.
 */
export { api, apiFetch, setAuthTokenProvider, setTokenRefresher } from "@/lib/api/client";
export type { ApiRequestOptions, QueryValue } from "@/lib/api/client";
export { ApiError } from "@/lib/api/errors";

export { proxyHosts, HTTP_SCHEMES } from "@/lib/api/resources/proxy-hosts";
export type {
  ProxyHost,
  ProxyHostCreate,
  ProxyHostUpdate,
  HttpScheme,
} from "@/lib/api/resources/proxy-hosts";

export { upstreams, LB_METHODS, LB_METHOD_LABELS } from "@/lib/api/resources/upstreams";
export type {
  Upstream,
  UpstreamCreate,
  UpstreamUpdate,
  Backend,
  BackendCreate,
  BackendUpdate,
  LoadBalanceMethod,
  UpstreamContext,
} from "@/lib/api/resources/upstreams";

export {
  certificates,
  ACME_CHALLENGES,
  CERT_PROVIDER_LABELS,
} from "@/lib/api/resources/certificates";
export type {
  Certificate,
  CustomCertificateCreate,
  LetsEncryptCertificateCreate,
  CertificateIssued,
  CertificateProvider,
  CertificateStatus,
  AcmeChallenge,
} from "@/lib/api/resources/certificates";

export {
  accessLists,
  ACCESS_LIST_DIRECTIVES,
  DIRECTIVE_LABELS,
} from "@/lib/api/resources/access-lists";
export type {
  AccessList,
  AccessListCreate,
  AccessListUpdate,
  AccessListAuthUser,
  AccessListAuthCreate,
  AccessListAuthUpdate,
  AccessListClientRule,
  AccessListClientCreate,
  AccessListClientUpdate,
  AccessListDirective,
} from "@/lib/api/resources/access-lists";

export { customPages, MAX_PAGE_BYTES } from "@/lib/api/resources/custom-pages";
export type {
  CustomPage,
  CustomPageSummary,
  CustomPageCreate,
  CustomPageUpdate,
  PageAssistRequest,
  PageAssistResponse,
  PageEditChange,
} from "@/lib/api/resources/custom-pages";

export { dashboard } from "@/lib/api/resources/dashboard";
export type {
  CertificateHealth,
  CountryCount,
  ConfigHealth,
  DashboardSummary,
  InventoryCounts,
  SecuritySummary,
  ThreatPoint,
  VisitorRow,
  VisitorSummary,
  TrafficSummary,
} from "@/lib/api/resources/dashboard";
export { instanceSettings } from "@/lib/api/resources/settings";
export type {
  CrowdSecBanMode,
  CrowdSecBanUpdate,
  CrowdSecCapiUpdate,
  CrowdSecHubUpdate,
  DefaultSiteMode,
  DefaultSiteUpdate,
  HubUpdateFrequency,
  InstanceSettings,
  LlmSettingsUpdate,
  LlmTestRequest,
  LlmTestResult,
  MailTestRequest,
  MailTestResult,
  SmtpSecurity,
  SmtpSettingsUpdate,
} from "@/lib/api/resources/settings";

export { streams } from "@/lib/api/resources/streams";
export type { Stream, StreamCreate, StreamUpdate } from "@/lib/api/resources/streams";

export {
  redirectionHosts,
  REDIRECT_SCHEMES,
  REDIRECT_HTTP_CODES,
  REDIRECT_CODE_LABELS,
} from "@/lib/api/resources/redirection-hosts";
export type {
  RedirectionHost,
  RedirectionHostCreate,
  RedirectionHostUpdate,
  RedirectScheme,
} from "@/lib/api/resources/redirection-hosts";

export { deadHosts } from "@/lib/api/resources/dead-hosts";
export type { DeadHost, DeadHostCreate, DeadHostUpdate } from "@/lib/api/resources/dead-hosts";

export { tasks, pollTask } from "@/lib/api/resources/tasks";
export type { TaskStatus, PollTaskOptions } from "@/lib/api/resources/tasks";

export {
  crowdsec,
  DECISION_SCOPES,
  DECISION_TYPES,
  DECISION_SCOPE_LABELS,
  DECISION_TYPE_LABELS,
  DECISION_DURATIONS,
  DEFAULT_PAGE_SIZE,
  PAGE_SIZE_OPTIONS,
  WHITELIST_KINDS,
  WHITELIST_KIND_LABELS,
} from "@/lib/api/resources/crowdsec";
export type {
  Alert,
  AlertList,
  AlertSource,
  CrowdSecHealth,
  CrowdSecJobRun,
  CrowdSecMaintenance,
  Decision,
  DecisionCreate,
  DecisionList,
  DecisionScope,
  DecisionType,
  ListParams,
  Whitelist,
  WhitelistApplyStatus,
  WhitelistCreate,
  WhitelistKind,
  WhitelistPreview,
  WhitelistUpdate,
} from "@/lib/api/resources/crowdsec";

export { users, USER_ROLES, USER_ROLE_LABELS } from "@/lib/api/resources/users";
export type {
  Passkey,
  PasskeyOptions,
  PasskeyRegister,
  PasswordChange,
  PasswordReset,
  ProfileUpdate,
  TotpCodes,
  TotpSetup,
  User,
  UserCreate,
  UserInvite,
  UserRole,
  UserUpdate,
} from "@/lib/api/resources/users";

export { dnsProviders, dnsCredentials } from "@/lib/api/resources/dns-providers";
export type {
  DnsProviderInfo,
  DnsProviderField,
  DnsCredential,
  DnsCredentialCreate,
  DnsCredentialUpdate,
  DnsCredentialVerify,
  DnsCredentialVerified,
} from "@/lib/api/resources/dns-providers";
