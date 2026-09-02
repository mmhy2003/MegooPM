import { getAccessToken } from "@/lib/auth/session";

/** Backoff between reconnects: 1s, 2s, 4s … capped. */
const FIRST_RETRY_MS = 1_000;
const MAX_RETRY_MS = 30_000;

function backoffFor(attempt: number): number {
  return Math.min(FIRST_RETRY_MS * 2 ** attempt, MAX_RETRY_MS);
}

/**
 * Parses SSE frames out of a growing buffer.
 *
 * Returns the event types found and whatever partial frame is left over: a
 * chunk boundary can fall anywhere, including mid-JSON, so the remainder has to
 * survive to the next read.
 *
 * Comment frames (`: keepalive`) carry no `data:` line and are ignored, which
 * is exactly what they are for.
 */
export function parseFrames(buffer: string): { types: string[]; rest: string } {
  const types: string[] = [];
  let rest = buffer;

  for (;;) {
    const end = rest.indexOf("\n\n");
    if (end === -1) break;
    const frame = rest.slice(0, end);
    rest = rest.slice(end + 2);

    for (const line of frame.split("\n")) {
      if (!line.startsWith("data: ")) continue;
      try {
        const parsed = JSON.parse(line.slice("data: ".length)) as { type?: string };
        if (parsed.type) types.push(parsed.type);
      } catch {
        // One malformed frame must not end a long-lived subscription.
      }
    }
  }

  return { types, rest };
}

/**
 * Subscribes to the dashboard event stream.
 *
 * Uses `fetch` rather than `EventSource` because `EventSource` cannot set an
 * `Authorization` header — it can only send cookies, and the session cookie is
 * host-only, so it never reaches an API deployed on a different host from the
 * UI. That produced a 401 on every connection in exactly the deployment shape
 * this product ships. `fetch` sends the same bearer token as every other call,
 * so the stream works wherever the API does.
 *
 * The cost is reconnection, which `EventSource` did for free: this reconnects
 * with capped exponential backoff instead.
 *
 * Returns an unsubscribe function and never throws. If the stream cannot be
 * opened at all, the caller's polling is what keeps the page fresh — push is an
 * accelerator here, never a dependency.
 */
export function subscribeToEvents(onEvent: (type: string) => void): () => void {
  if (typeof fetch === "undefined" || typeof AbortController === "undefined") {
    return () => {};
  }

  let stopped = false;
  let controller: AbortController | null = null;
  let attempt = 0;

  async function run(): Promise<void> {
    while (!stopped) {
      controller = new AbortController();
      try {
        const token = getAccessToken();
        if (!token) throw new Error("no session");

        const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
        const response = await fetch(`${base}/api/v1/events`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: controller.signal,
        });
        if (!response.ok || !response.body) throw new Error(String(response.status));

        // Connected: reset the backoff so a long-lived stream that drops once
        // reconnects promptly rather than inheriting an old penalty.
        attempt = 0;

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        for (;;) {
          const { done, value } = await reader.read();
          if (done || stopped) break;
          buffer += decoder.decode(value, { stream: true });
          const { types, rest } = parseFrames(buffer);
          buffer = rest;
          for (const type of types) onEvent(type);
        }
      } catch {
        // Any failure — network, 401, abort — falls through to the backoff.
        // Never rethrown: this runs unawaited, and an unhandled rejection here
        // would surface as a console error on a page that is working fine.
      }

      if (stopped) return;
      await new Promise((resolve) => setTimeout(resolve, backoffFor(attempt)));
      attempt = Math.min(attempt + 1, 5);
    }
  }

  void run();

  return () => {
    stopped = true;
    controller?.abort();
  };
}
