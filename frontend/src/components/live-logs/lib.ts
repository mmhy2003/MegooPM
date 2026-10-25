/**
 * Shapes and pure helpers for the Live logs page.
 *
 * Everything here is text from the wire: URLs and user agents are chosen by
 * whoever sent the request, so nothing is ever interpreted as HTML.
 */

export type LogKind = "request" | "error" | "security" | "stream" | "decision";
export type Tab = "requests" | "errors" | "security" | "streams";

/** One entry in a tab's buffer. `f` holds the request or stream fields. */
export interface LiveLine {
  /** Local, increasing: a stable React key. */
  id: number;
  /** When the page received it (ms): the time shown for lines that carry none. */
  at: number;
  node: string;
  host: string;
  log: LogKind;
  sev: number;
  f: Record<string, string> | null;
  raw: string | null;
  truncated: boolean;
  cc: string | null;
}

/** The frames the server sends. */
export type LiveFrame =
  | {
      type: "line";
      node: string;
      host: string;
      log: LogKind;
      sev?: number;
      f?: Record<string, string> | null;
      raw?: string | null;
      truncated?: boolean;
      cc?: string | null;
    }
  | {
      type: "decision";
      value?: string;
      scope?: string;
      type_?: string;
      scenario?: string;
      duration?: string;
      origin?: string;
      cc?: string | null;
    }
  | { type: "skipped"; count: number }
  | { type: "ping" };

export const TABS: readonly Tab[] = ["requests", "errors", "security", "streams"] as const;

export const TAB_OF: Record<LiveLine["log"], Tab> = {
  request: "requests",
  error: "errors",
  security: "security",
  decision: "security",
  stream: "streams",
};

/** How many lines each tab keeps. */
export const LINE_CAP = 1000;

export function appendCapped(lines: LiveLine[], next: LiveLine[], cap = LINE_CAP): LiveLine[] {
  const all = lines.concat(next);
  return all.length > cap ? all.slice(all.length - cap) : all;
}

/** `"2xx"` … `"5xx"` for a status, or `null` when it is not one. */
export function statusClass(st: string | number | null | undefined): string | null {
  const n = typeof st === "number" ? st : Number.parseInt(st ?? "", 10);
  if (!Number.isInteger(n) || n < 100 || n > 599) return null;
  return `${Math.floor(n / 100)}xx`;
}

export interface LineFilter {
  node: string;
  status: string;
  text: string;
}

/** Whether `line` passes the node, status-class and free-text filters. */
export function matches(line: LiveLine, filter: LineFilter): boolean {
  if (filter.node && line.node !== filter.node) return false;
  if (filter.status && statusClass(line.f?.st) !== filter.status) return false;
  const needle = filter.text.trim().toLowerCase();
  if (needle) {
    const haystack = [line.f?.u, line.f?.ip, line.f?.ua, line.raw];
    if (!haystack.some((part) => part?.toLowerCase().includes(needle))) return false;
  }
  return true;
}

/** `HH:MM:SS.mmm` in local time, from the line's own timestamp when it has one. */
export function lineTime(line: LiveLine): string {
  const seconds = Number.parseFloat(line.f?.ts ?? "");
  const date = new Date(Number.isFinite(seconds) ? seconds * 1000 : line.at);
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${p(date.getHours())}:${p(date.getMinutes())}:${p(date.getSeconds())}.${p(date.getMilliseconds(), 3)}`;
}

/** Plain text, one entry per line, for the Save button. */
export function toText(lines: LiveLine[]): string {
  return lines
    .map((line) => {
      const time = lineTime(line);
      const f = line.f;
      if (!f) return `${time} ${line.node} ${line.host} ${line.raw ?? ""}`;
      if (line.log === "stream") {
        return `${time} ${line.node} ${line.host} ${f.ip ?? ""} ${f.pr ?? ""} ${f.st ?? ""} in=${f.in ?? ""} out=${f.out ?? ""} ${f.dur ?? ""}s`;
      }
      if (line.log === "decision") {
        return `${time} ${line.node} ${f.type ?? ""} ${f.scope ?? ""} ${f.value ?? ""} ${f.scenario ?? ""} ${f.duration ?? ""}`;
      }
      return `${time} ${line.node} ${line.host} ${f.st ?? ""} ${f.m ?? ""} ${f.u ?? ""} ${f.ip ?? ""} ${f.rt ?? ""}s`;
    })
    .join("\n");
}
