import { API_BASE_URL } from "@/lib/env";
import { ApiError } from "@/lib/api/errors";

/**
 * Typed HTTP client for the MegooPM backend (FastAPI).
 *
 * This is the foundation layer: it owns URL construction, JSON
 * (de)serialization, auth-header injection and error normalization. Feature
 * tickets bind concrete, typed endpoints on top of {@link apiFetch} — e.g.
 *
 * ```ts
 * export const proxyHosts = {
 *   list: () => apiFetch<ProxyHost[]>("/api/proxy-hosts"),
 *   create: (body: ProxyHostCreate) =>
 *     apiFetch<ProxyHost>("/api/proxy-hosts", { method: "POST", body }),
 * };
 * ```
 */

export type QueryValue = string | number | boolean | null | undefined;

export interface ApiRequestOptions extends Omit<RequestInit, "body"> {
  /** Query-string params; `null`/`undefined` values are dropped. */
  query?: Record<string, QueryValue>;
  /** JSON-serializable request body. Sets `Content-Type: application/json`. */
  body?: unknown;
  /** Bearer token override; defaults to {@link getAuthToken}. */
  token?: string | null;
}

/**
 * Resolves the auth token for a request. Swapped out for a real session store
 * when the auth ticket lands; kept here so the client has a single seam.
 */
let authTokenProvider: () => string | null = () => null;

export function setAuthTokenProvider(provider: () => string | null): void {
  authTokenProvider = provider;
}

function getAuthToken(): string | null {
  return authTokenProvider();
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const base = API_BASE_URL.replace(/\/$/, "");
  const url = new URL(
    path.startsWith("http") ? path : `${base}${path.startsWith("/") ? path : `/${path}`}`,
  );
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== null && value !== undefined) {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.toString();
}

/**
 * Perform a typed request against the backend.
 *
 * @typeParam T - expected shape of the parsed JSON response.
 * @throws {ApiError} for any non-2xx response.
 */
export async function apiFetch<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const { query, body, token, headers, ...init } = options;

  const finalHeaders = new Headers(headers);
  finalHeaders.set("Accept", "application/json");

  const resolvedToken = token === undefined ? getAuthToken() : token;
  if (resolvedToken) {
    finalHeaders.set("Authorization", `Bearer ${resolvedToken}`);
  }

  let serializedBody: BodyInit | undefined;
  if (body !== undefined) {
    finalHeaders.set("Content-Type", "application/json");
    serializedBody = JSON.stringify(body);
  }

  const response = await fetch(buildUrl(path, query), {
    ...init,
    headers: finalHeaders,
    body: serializedBody,
  });

  const payload = await parseBody(response);

  if (!response.ok) {
    throw new ApiError(
      response.status,
      `${init.method ?? "GET"} ${path} failed with ${response.status}`,
      payload,
    );
  }

  return payload as T;
}

async function parseBody(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined;
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  const text = await response.text();
  return text.length ? text : undefined;
}

/** Convenience verb helpers over {@link apiFetch}. */
export const api = {
  get: <T>(path: string, options?: ApiRequestOptions) =>
    apiFetch<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, body?: unknown, options?: ApiRequestOptions) =>
    apiFetch<T>(path, { ...options, method: "POST", body }),
  put: <T>(path: string, body?: unknown, options?: ApiRequestOptions) =>
    apiFetch<T>(path, { ...options, method: "PUT", body }),
  patch: <T>(path: string, body?: unknown, options?: ApiRequestOptions) =>
    apiFetch<T>(path, { ...options, method: "PATCH", body }),
  delete: <T>(path: string, options?: ApiRequestOptions) =>
    apiFetch<T>(path, { ...options, method: "DELETE" }),
} as const;
