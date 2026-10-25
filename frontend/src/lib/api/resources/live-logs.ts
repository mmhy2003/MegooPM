/**
 * Live logs: a ticket from the API, and the socket it opens.
 *
 * A browser cannot put a bearer header on a WebSocket, so the page asks for a
 * short-lived ticket first and passes it in the query string. Nothing is
 * stored: see `components/live-logs`.
 */
import { api } from "@/lib/api/client";
import { API_BASE_URL } from "@/lib/env";

export interface LiveTicket {
  ticket: string;
  expires_in: number;
}

export const liveLogs = {
  ticket: () => api.post<LiveTicket>("/api/v1/live-logs/ticket"),
} as const;

/** The socket's URL: the API's origin with its scheme swapped for ws/wss. */
export function liveSocketUrl(ticket: string): string {
  const base = API_BASE_URL.replace(/\/$/, "").replace(/^http/, "ws");
  return `${base}/api/v1/live-logs/ws?ticket=${encodeURIComponent(ticket)}`;
}
