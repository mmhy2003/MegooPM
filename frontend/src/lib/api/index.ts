/**
 * Public entrypoint for the MegooPM API client.
 *
 * Feature tickets add resource modules under `src/lib/api/resources/` and
 * re-export them here so callers import from a single, stable path:
 * `import { api, apiFetch } from "@/lib/api"`.
 */
export {
  api,
  apiFetch,
  setAuthTokenProvider,
  setTokenRefresher,
} from "@/lib/api/client";
export type { ApiRequestOptions, QueryValue } from "@/lib/api/client";
export { ApiError } from "@/lib/api/errors";

export { proxyHosts, HTTP_SCHEMES } from "@/lib/api/resources/proxy-hosts";
export type {
  ProxyHost,
  ProxyHostCreate,
  ProxyHostUpdate,
  HttpScheme,
} from "@/lib/api/resources/proxy-hosts";

export {
  upstreams,
  LB_METHODS,
  LB_METHOD_LABELS,
} from "@/lib/api/resources/upstreams";
export type {
  Upstream,
  UpstreamCreate,
  UpstreamUpdate,
  Backend,
  BackendCreate,
  BackendUpdate,
  LoadBalanceMethod,
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
