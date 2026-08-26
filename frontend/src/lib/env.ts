/**
 * Centralized, typed access to public environment variables.
 *
 * Only `NEXT_PUBLIC_*` variables are readable in the browser; they are inlined
 * at build time by Next.js, so they must be referenced statically (not via a
 * dynamic key) for the replacement to happen.
 */

function optional(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

/**
 * Base URL of the MegooPM backend API (FastAPI).
 *
 * Defaults to a local dev backend so `npm run dev` works with no `.env`.
 * Override with `NEXT_PUBLIC_API_BASE_URL` in `.env.local` / deployment env.
 */
export const API_BASE_URL: string =
  optional(process.env.NEXT_PUBLIC_API_BASE_URL) ?? "http://localhost:8000";

export const APP_NAME = "MegooPM";
