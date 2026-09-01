/**
 * Pure helpers for the Settings page.
 *
 * Kept free of React so the mode branching — which field each mode needs, what
 * gets sent, what is rejected — stays unit-testable without mounting the card.
 *
 * Validation here is a courtesy that catches mistakes before a round trip; the
 * backend is the authority, and its redirect-URL rules are deliberately
 * stricter (it rejects characters that could escape an nginx directive).
 */
import type { DefaultSiteMode, InstanceSettings, InstanceSettingsUpdate } from "@/lib/api";

export { describeError } from "@/components/proxy-hosts/lib";

/** The modes, in the order the radio group shows them. */
export const DEFAULT_SITE_MODES: readonly DefaultSiteMode[] = [
  "congratulations",
  "not_found",
  "no_response",
  "redirect",
  "custom_page",
] as const;

export const DEFAULT_SITE_MODE_LABELS: Record<DefaultSiteMode, string> = {
  congratulations: "Congratulations page",
  not_found: "404 page",
  no_response: "No response (444)",
  redirect: "Redirect",
  custom_page: "Custom page",
};

export const DEFAULT_SITE_MODE_HINTS: Record<DefaultSiteMode, string> = {
  congratulations: "A branded MegooPM page saying the host isn't configured yet.",
  not_found: "A bare 404, with no body. What MegooPM serves today.",
  no_response: "Close the connection without answering. Hides that anything is listening.",
  redirect: "Send the visitor somewhere else with a 301.",
  custom_page: "Serve one of the pages from Custom Pages.",
};

export type SettingsFormState = {
  mode: DefaultSiteMode;
  redirectUrl: string;
  pageId: number | null;
};

export function emptyFormState(): SettingsFormState {
  return { mode: "not_found", redirectUrl: "", pageId: null };
}

export function stateFromSettings(settings: InstanceSettings): SettingsFormState {
  return {
    mode: settings.default_site_mode,
    // Null becomes "" so the input stays controlled across a mode switch.
    redirectUrl: settings.default_site_redirect_url ?? "",
    pageId: settings.default_site_page_id ?? null,
  };
}

/** The first problem blocking a save, or `null` when the form is ready. */
export function validateSettingsForm(state: SettingsFormState): string | null {
  if (state.mode === "redirect") {
    const url = state.redirectUrl.trim();
    if (!url) return "Enter the URL to redirect to.";
    if (!/^https?:\/\//i.test(url)) return "The URL must start with http:// or https://.";
  }
  if (state.mode === "custom_page" && state.pageId === null) {
    return "Choose a custom page to serve.";
  }
  return null;
}

/**
 * Only the field the chosen mode uses is sent; the others are explicitly
 * `null`. The backend clears them anyway, but sending stale values would make
 * the request describe a configuration nobody asked for.
 */
export function buildDefaultSitePayload(state: SettingsFormState): InstanceSettingsUpdate {
  return {
    default_site_mode: state.mode,
    default_site_redirect_url: state.mode === "redirect" ? state.redirectUrl.trim() : null,
    default_site_page_id: state.mode === "custom_page" ? state.pageId : null,
  };
}
