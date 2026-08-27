/**
 * Domain-name helpers shared by every "domain names" field (React-free).
 *
 * `isValidDomain` mirrors the backend's lenient hostname rule
 * (`app/schemas/proxy_host.py::_DOMAIN_RE`): labels of letters, digits and
 * inner hyphens, optionally one leading wildcard label. Single-label names
 * (`localhost`) are valid, as they are server-side. nginx stays the final
 * authority when the config is applied.
 */

const DOMAIN_RE =
  /^(?:\*\.)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;

export function normalizeDomain(raw: string): string {
  return raw.trim().toLowerCase();
}

export function isValidDomain(name: string): boolean {
  return DOMAIN_RE.test(name);
}

/**
 * Parse free text into a normalised domain list: comma-, whitespace- or
 * newline-separated, trimmed, lower-cased, de-duplicated (no validation).
 */
export function parseDomains(input: string): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const raw of input.split(/[\s,]+/)) {
    const name = normalizeDomain(raw);
    if (name && !seen.has(name)) {
      seen.add(name);
      result.push(name);
    }
  }
  return result;
}

/**
 * Add the domains in `text` to `existing`: valid, new names are appended in
 * order; invalid ones are returned in `rejected` so the UI can keep them in
 * the input with an error instead of dropping them silently.
 */
export function addDomains(
  existing: string[],
  text: string,
): { next: string[]; rejected: string[] } {
  const next = [...existing];
  const rejected: string[] = [];
  for (const raw of text.split(/[\s,]+/)) {
    const name = normalizeDomain(raw);
    if (!name) continue;
    if (!isValidDomain(name)) {
      rejected.push(name);
      continue;
    }
    if (!next.includes(name)) next.push(name);
  }
  return { next, rejected };
}
