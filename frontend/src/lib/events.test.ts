import { afterEach, describe, expect, it, vi } from "vitest";

import { parseFrames, subscribeToEvents } from "@/lib/events";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("parseFrames", () => {
  it("reads one complete frame", () => {
    const { types, rest } = parseFrames('data: {"type":"config.changed"}\n\n');
    expect(types).toEqual(["config.changed"]);
    expect(rest).toBe("");
  });

  it("keeps a partial frame for the next read", () => {
    // A chunk boundary can fall anywhere, including mid-JSON. Dropping the
    // remainder would silently lose an event on every awkward split.
    const { types, rest } = parseFrames('data: {"type":"a"}\n\ndata: {"ty');
    expect(types).toEqual(["a"]);
    expect(rest).toBe('data: {"ty');
  });

  it("reads several frames from one chunk", () => {
    const { types } = parseFrames(
      'data: {"type":"a"}\n\ndata: {"type":"b"}\n\n',
    );
    expect(types).toEqual(["a", "b"]);
  });

  it("ignores keepalive comments", () => {
    // They exist to keep proxies from closing an idle stream, not to signal.
    const { types } = parseFrames(': keepalive\n\ndata: {"type":"a"}\n\n');
    expect(types).toEqual(["a"]);
  });

  it("ignores a malformed frame rather than throwing", () => {
    const { types } = parseFrames('data: not json\n\ndata: {"type":"a"}\n\n');
    expect(types).toEqual(["a"]);
  });

  it("ignores a frame with no type", () => {
    expect(parseFrames('data: {"detail":{}}\n\n').types).toEqual([]);
  });
});

describe("subscribeToEvents", () => {
  it("sends the bearer token, not a cookie", async () => {
    // The whole reason this is fetch and not EventSource: the session cookie
    // is host-only, so it never reaches an API on a different host and every
    // connection was rejected with 401.
    vi.mock("@/lib/auth/session", () => ({ getAccessToken: () => "tok123" }));

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      body: { getReader: () => ({ read: async () => ({ done: true }) }) },
    });
    vi.stubGlobal("fetch", fetchMock);

    const stop = subscribeToEvents(() => {});
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    stop();

    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers.Authorization).toBe("Bearer tok123");
  });

  it("delivers event types to the callback", async () => {
    vi.mock("@/lib/auth/session", () => ({ getAccessToken: () => "tok123" }));

    const chunks = [
      new TextEncoder().encode('data: {"type":"config.changed"}\n\n'),
    ];
    let index = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        body: {
          getReader: () => ({
            read: async () =>
              index < chunks.length
                ? { done: false, value: chunks[index++] }
                : { done: true },
          }),
        },
      }),
    );

    const seen: string[] = [];
    const stop = subscribeToEvents((type) => seen.push(type));
    await vi.waitFor(() => expect(seen).toEqual(["config.changed"]));
    stop();
  });

  it("stops cleanly and never throws", () => {
    // It runs unawaited, so a rejection here would surface as a console error
    // on a page that is otherwise working.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("network down")),
    );
    expect(() => subscribeToEvents(() => {})()).not.toThrow();
  });

  it("does nothing when fetch is unavailable", () => {
    // The dashboard must still poll.
    vi.stubGlobal("fetch", undefined);
    expect(() => subscribeToEvents(() => {})()).not.toThrow();
  });
});
