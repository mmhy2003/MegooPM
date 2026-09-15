/**
 * Number formatting for figures that can grow large.
 *
 * Pinned to English rather than the browser's language: K/M/B are what every
 * operator of one instance should read on the same dashboard, and a German or
 * Arabic locale would change the separators, the suffixes, or the digits.
 */
const COMPACT = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const EXACT = new Intl.NumberFormat("en");

/** 999 → "999", 24270 → "24.3K", 999950 → "1M", 1266434 → "1.3M". */
export function formatCount(value: number): string {
  return COMPACT.format(value);
}

/** 1266434 → "1,266,434": the precision a shortened figure gives up. */
export function formatExact(value: number): string {
  return EXACT.format(value);
}
