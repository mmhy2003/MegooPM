import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

import { useLiveLogs } from "@/components/live-logs/use-live-logs";

const ticket = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api/resources/live-logs", () => ({
  liveLogs: { ticket },
  liveSocketUrl: (t: string) => `ws://api/ws?ticket=${t}`,
}));

class FakeSocket {
  static OPEN = 1;
  static all: FakeSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  constructor(public url: string) {
    FakeSocket.all.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.closed = true;
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  push(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
  drop(code: number) {
    this.onclose?.({ code });
  }
}

const reqLine = (n: number) => ({
  type: "line",
  node: "local",
  host: "p1",
  log: "request",
  sev: 6,
  f: { st: "200", m: "GET", u: `/${n}`, ip: "1.1.1.1" },
  raw: null,
  truncated: false,
  cc: "DE",
});

async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(200);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeSocket.all = [];
  let n = 0;
  ticket.mockReset();
  ticket.mockImplementation(async () => ({ ticket: `t${++n}`, expires_in: 30 }));
  vi.stubGlobal("WebSocket", FakeSocket);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function connect(watch = { hosts: ["p1"], decisions: false }) {
  const hook = renderHook((w) => useLiveLogs(w), { initialProps: watch });
  await flush();
  const sock = FakeSocket.all[0]!;
  act(() => sock.open());
  return { hook, sock };
}

describe("useLiveLogs", () => {
  it("opens the socket with a ticket and sends the watch frame on open", async () => {
    const { hook, sock } = await connect();
    expect(sock.url).toBe("ws://api/ws?ticket=t1");
    expect(JSON.parse(sock.sent[0]!)).toEqual({ type: "watch", hosts: ["p1"], decisions: false });
    expect(hook.result.current.status).toBe("live");
  });

  it("sends a new watch frame when the selection changes", async () => {
    const { hook, sock } = await connect();
    hook.rerender({ hosts: ["p1", "u"], decisions: true });
    expect(JSON.parse(sock.sent[1]!)).toEqual({
      type: "watch",
      hosts: ["p1", "u"],
      decisions: true,
    });
  });

  it("files lines by tab, decisions under security, and counts skipped", async () => {
    const { hook, sock } = await connect();
    act(() => {
      sock.push(reqLine(1));
      sock.push({ type: "decision", value: "9.9.9.9", scope: "Ip", type_: "ban", cc: null });
      sock.push({ type: "skipped", count: 7 });
      sock.push({ type: "ping" });
    });
    await flush();
    expect(hook.result.current.lines.requests).toHaveLength(1);
    expect(hook.result.current.lines.security[0]!.log).toBe("decision");
    expect(hook.result.current.skipped).toBe(7);
  });

  it("keeps buffering while paused and shows the lines on resume", async () => {
    const { hook, sock } = await connect();
    act(() => hook.result.current.setPaused(true));
    act(() => {
      sock.push(reqLine(1));
      sock.push(reqLine(2));
    });
    await flush();
    expect(hook.result.current.lines.requests).toHaveLength(0);
    expect(hook.result.current.pending).toBe(2);
    act(() => hook.result.current.setPaused(false));
    await flush();
    expect(hook.result.current.lines.requests).toHaveLength(2);
    expect(hook.result.current.pending).toBe(0);
  });

  it("clears the buffers", async () => {
    const { hook, sock } = await connect();
    act(() => sock.push(reqLine(1)));
    await flush();
    act(() => hook.result.current.clear());
    expect(hook.result.current.lines.requests).toHaveLength(0);
  });

  it("reconnects with a new ticket after a close, backing off", async () => {
    const { hook, sock } = await connect();
    act(() => sock.drop(1006));
    expect(hook.result.current.status).toBe("reconnecting");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500); // 1 s has not passed
    });
    expect(FakeSocket.all).toHaveLength(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(FakeSocket.all).toHaveLength(2);
    expect(FakeSocket.all[1]!.url).toBe("ws://api/ws?ticket=t2");
  });

  it("gets a new ticket straight away on 4401", async () => {
    const { sock } = await connect();
    act(() => sock.drop(4401));
    await flush();
    expect(FakeSocket.all).toHaveLength(2);
    expect(ticket).toHaveBeenCalledTimes(2);
  });

  it("stops on 4403 and reports revoked access", async () => {
    const { hook, sock } = await connect();
    act(() => sock.drop(4403));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(hook.result.current.status).toBe("revoked");
    expect(FakeSocket.all).toHaveLength(1);
  });

  it("stops on 4400 and reports an error instead of looping", async () => {
    const { hook, sock } = await connect();
    act(() => sock.drop(4400));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(hook.result.current.status).toBe("error");
    expect(FakeSocket.all).toHaveLength(1);
  });

  it("closes the socket on unmount and does not reconnect", async () => {
    const { hook, sock } = await connect();
    hook.unmount();
    expect(sock.closed).toBe(true);
    sock.drop(1006);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(FakeSocket.all).toHaveLength(1);
  });
});
