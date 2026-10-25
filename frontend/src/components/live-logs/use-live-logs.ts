"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  appendCapped,
  TAB_OF,
  type LiveFrame,
  type LiveLine,
  type Tab,
} from "@/components/live-logs/lib";
import { ApiError } from "@/lib/api/errors";
import { liveLogs, liveSocketUrl } from "@/lib/api/resources/live-logs";

export type LiveStatus = "connecting" | "live" | "reconnecting" | "revoked" | "error";

export interface Watch {
  hosts: string[];
  decisions: boolean;
}

const BACKOFF_START_MS = 1000;
const BACKOFF_MAX_MS = 30_000;
/** Lines are shown in batches, so a busy host re-renders ten times a second, not per line. */
const FLUSH_MS = 100;

const CLOSE_BAD_FRAME = 4400;
const CLOSE_UNAUTHORIZED = 4401;
const CLOSE_FORBIDDEN = 4403;

type Buffers = Record<Tab, LiveLine[]>;

function emptyBuffers(): Buffers {
  return { requests: [], errors: [], security: [], streams: [] };
}

function stringFields(source: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(source)) {
    if (value !== null && value !== undefined) out[key] = String(value);
  }
  return out;
}

/** A frame as a buffer entry; `null` for the frames that carry no line. */
function toLine(frame: LiveFrame, id: number): LiveLine | null {
  const at = Date.now();
  if (frame.type === "line") {
    return {
      id,
      at,
      node: frame.node ?? "local",
      host: frame.host ?? "",
      log: frame.log,
      sev: frame.sev ?? 6,
      f: frame.f ? stringFields(frame.f) : null,
      raw: frame.raw ?? null,
      truncated: frame.truncated === true,
      cc: frame.cc ?? null,
    };
  }
  if (frame.type === "decision") {
    return {
      id,
      at,
      node: "local",
      host: "",
      log: "decision",
      sev: 6,
      f: stringFields({
        value: frame.value,
        scope: frame.scope,
        type: frame.type_,
        scenario: frame.scenario,
        duration: frame.duration,
        origin: frame.origin,
      }),
      raw: null,
      truncated: false,
      cc: frame.cc ?? null,
    };
  }
  return null;
}

/**
 * Connects to the live-logs socket and keeps the last lines of each tab.
 *
 * Lines live in this hook's memory only. A close is answered by the code:
 * 4401 gets a new ticket at once, 4403 and 4400 stop for good (access was
 * revoked / the page sent a frame the server rejected), anything else
 * reconnects with a growing delay and a fresh ticket.
 *
 * Pause stops what is rendered, not what is kept: lines go on arriving into
 * the buffers, and `pending` says how many wait.
 */
export function useLiveLogs(watch: Watch) {
  const [status, setStatus] = useState<LiveStatus>("connecting");
  const [lines, setLines] = useState<Buffers>(emptyBuffers);
  const [skipped, setSkipped] = useState(0);
  const [pending, setPending] = useState(0);
  const [rate, setRate] = useState(0);
  const [paused, setPausedState] = useState(false);

  const buffers = useRef<Buffers>(emptyBuffers());
  const pausedRef = useRef(false);
  const pendingRef = useRef(0);
  const requestsThisSecond = useRef(0);
  const nextId = useRef(1);
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const socket = useRef<WebSocket | null>(null);
  const watchRef = useRef<Watch>(watch);

  const hostsKey = JSON.stringify(watch.hosts);
  const { decisions } = watch;

  const flush = useCallback(() => {
    flushTimer.current = null;
    if (pausedRef.current) {
      setPending(pendingRef.current);
      return;
    }
    pendingRef.current = 0;
    setLines({ ...buffers.current });
    setPending(0);
  }, []);

  const scheduleFlush = useCallback(() => {
    if (flushTimer.current === null) flushTimer.current = setTimeout(flush, FLUSH_MS);
  }, [flush]);

  const setPaused = useCallback(
    (next: boolean) => {
      pausedRef.current = next;
      setPausedState(next);
      if (!next) scheduleFlush();
    },
    [scheduleFlush],
  );

  const clear = useCallback(() => {
    buffers.current = emptyBuffers();
    pendingRef.current = 0;
    setLines(emptyBuffers());
    setPending(0);
    setSkipped(0);
  }, []);

  // The watch, as the server should see it. Declared before the connection
  // effect so a socket that opens finds the current selection.
  useEffect(() => {
    watchRef.current = { hosts: JSON.parse(hostsKey) as string[], decisions };
    const open = socket.current;
    if (open && open.readyState === WebSocket.OPEN) {
      open.send(JSON.stringify({ type: "watch", ...watchRef.current }));
    }
  }, [hostsKey, decisions]);

  useEffect(() => {
    let stopped = false;
    let attempt = 0;
    let lastWas401 = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const retryIn = (ms: number) => {
      setStatus("reconnecting");
      retryTimer = setTimeout(() => void connect(), ms);
    };
    const backoff = () => {
      const delay = Math.min(BACKOFF_START_MS * 2 ** attempt, BACKOFF_MAX_MS);
      attempt += 1;
      retryIn(delay);
    };

    const handleFrame = (frame: LiveFrame) => {
      if (frame.type === "ping") return;
      if (frame.type === "skipped") {
        setSkipped((n) => n + frame.count);
        return;
      }
      const line = toLine(frame, nextId.current++);
      if (!line) return;
      const tab = TAB_OF[line.log];
      if (!tab) return;
      buffers.current[tab] = appendCapped(buffers.current[tab], [line]);
      if (line.log === "request") requestsThisSecond.current += 1;
      if (pausedRef.current) pendingRef.current += 1;
      scheduleFlush();
    };

    async function connect() {
      if (stopped) return;
      let ticket: string;
      try {
        ticket = (await liveLogs.ticket()).ticket;
      } catch (err) {
        if (stopped) return;
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
          setStatus("revoked");
          return;
        }
        backoff();
        return;
      }
      if (stopped) return;
      const ws = new WebSocket(liveSocketUrl(ticket));
      socket.current = ws;
      ws.onopen = () => {
        setStatus("live");
        ws.send(JSON.stringify({ type: "watch", ...watchRef.current }));
      };
      ws.onmessage = (event) => {
        let frame: LiveFrame;
        try {
          frame = JSON.parse(String(event.data)) as LiveFrame;
        } catch {
          return;
        }
        // A frame proves the ticket worked: the next failure starts the delay over.
        attempt = 0;
        lastWas401 = false;
        handleFrame(frame);
      };
      ws.onclose = (event) => {
        if (socket.current === ws) socket.current = null;
        if (stopped) return;
        if (event.code === CLOSE_FORBIDDEN) {
          setStatus("revoked");
        } else if (event.code === CLOSE_BAD_FRAME) {
          setStatus("error");
        } else if (event.code === CLOSE_UNAUTHORIZED && !lastWas401) {
          // The ticket expired or was refused: a new one, straight away, once.
          lastWas401 = true;
          retryIn(0);
        } else {
          backoff();
        }
      };
    }

    void connect();
    return () => {
      stopped = true;
      if (retryTimer) clearTimeout(retryTimer);
      const ws = socket.current;
      socket.current = null;
      ws?.close();
    };
  }, [scheduleFlush]);

  // Requests per second, for the live dot.
  useEffect(() => {
    const timer = setInterval(() => {
      setRate(requestsThisSecond.current);
      requestsThisSecond.current = 0;
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(
    () => () => {
      if (flushTimer.current) clearTimeout(flushTimer.current);
    },
    [],
  );

  return { status, lines, skipped, pending, rate, paused, setPaused, clear };
}
