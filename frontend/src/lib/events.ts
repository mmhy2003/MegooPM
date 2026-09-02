/**
 * Subscribes to the dashboard event stream.
 *
 * `EventSource` sends the session cookie automatically and reconnects on its
 * own — most of why SSE was chosen over WebSocket, which would have needed both
 * written by hand.
 *
 * Returns an unsubscribe function, and never throws. If the stream cannot be
 * opened at all, the caller's polling is what keeps the page fresh: push is an
 * accelerator here, never a dependency.
 */
export function subscribeToEvents(onEvent: (type: string) => void): () => void {
  if (typeof EventSource === "undefined") return () => {};

  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
  let source: EventSource;
  try {
    // withCredentials so the session cookie is sent: EventSource cannot set an
    // Authorization header, which is why the endpoint accepts the cookie.
    source = new EventSource(`${base}/api/v1/events`, { withCredentials: true });
  } catch {
    return () => {};
  }

  source.onmessage = (message: MessageEvent<string>) => {
    try {
      const parsed = JSON.parse(message.data) as { type?: string };
      if (parsed.type) onEvent(parsed.type);
    } catch {
      // One malformed frame must not end a long-lived subscription.
    }
  };

  // Deliberately no onerror handler that closes the source: EventSource
  // reconnects by itself, and closing here would turn a momentary blip into a
  // permanently dead stream.

  return () => source.close();
}
