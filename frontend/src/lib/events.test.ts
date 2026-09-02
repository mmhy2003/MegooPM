import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { subscribeToEvents } from "@/lib/events";

class FakeEventSource {
  static last: FakeEventSource | null = null;

  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closed = false;
  readonly url: string;
  readonly withCredentials: boolean;

  constructor(url: string, init?: { withCredentials?: boolean }) {
    this.url = url;
    this.withCredentials = Boolean(init?.withCredentials);
    FakeEventSource.last = this;
  }

  close() {
    this.closed = true;
  }

  emit(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>);
  }

  emitRaw(data: string) {
    this.onmessage?.({ data } as MessageEvent<string>);
  }
}

beforeEach(() => {
  FakeEventSource.last = null;
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("subscribeToEvents", () => {
  it("calls back with the event type", () => {
    const seen: string[] = [];
    const stop = subscribeToEvents((type) => seen.push(type));

    FakeEventSource.last!.emit({ type: "config.changed", at: "", detail: {} });

    expect(seen).toEqual(["config.changed"]);
    stop();
  });

  it("sends credentials, because the cookie is the only way to authenticate", () => {
    // EventSource cannot set an Authorization header; without this the stream
    // is rejected and the dashboard silently falls back to polling forever.
    subscribeToEvents(() => {})();
    expect(FakeEventSource.last!.withCredentials).toBe(true);
  });

  it("ignores a malformed frame rather than throwing", () => {
    // The stream is long-lived: one bad frame must not kill the subscription.
    const seen: string[] = [];
    subscribeToEvents((type) => seen.push(type));

    FakeEventSource.last!.emitRaw("not json");
    FakeEventSource.last!.emit({ type: "config.changed" });

    expect(seen).toEqual(["config.changed"]);
  });

  it("ignores a frame with no type", () => {
    const seen: string[] = [];
    subscribeToEvents((type) => seen.push(type));
    FakeEventSource.last!.emit({ detail: {} });
    expect(seen).toEqual([]);
  });

  it("closes the connection when stopped", () => {
    // A leaked connection per mount, on a page navigated in and out all day.
    const stop = subscribeToEvents(() => {});
    stop();
    expect(FakeEventSource.last!.closed).toBe(true);
  });

  it("does nothing when EventSource is unavailable", () => {
    // Older browsers and some test environments. The dashboard must still poll.
    vi.stubGlobal("EventSource", undefined);
    expect(() => subscribeToEvents(() => {})()).not.toThrow();
  });
});
