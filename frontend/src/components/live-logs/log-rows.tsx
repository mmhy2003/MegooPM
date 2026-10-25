import { memo, type CSSProperties } from "react";

import { lineTime, statusClass, type LiveLine, type Tab } from "@/components/live-logs/lib";
import { Badge } from "@/components/ui/badge";
import { CountryFlag } from "@/components/ui/country-flag";
import { cn } from "@/lib/utils";

/** Every value below is text from the wire. It is rendered as a React child, never as HTML. */

const STATUS_COLOUR: Record<string, string> = {
  "2xx": "text-success",
  "3xx": "text-primary",
  "4xx": "text-warning",
  "5xx": "text-destructive",
};

interface Column {
  label: string;
  width: string;
}

// Narrows with the table (down to 6rem), so on a small screen the path or
// message column keeps its room.
const HOST: Column = { label: "Host", width: "clamp(6rem,18%,9rem)" };

/**
 * Whether Requests or Stream hosts get a Host column: only when the page says
 * the view can mix hosts. Errors and Security always have one of their own.
 */
function hostColumn(tab: Tab, showHost: boolean): boolean {
  return showHost && (tab === "requests" || tab === "streams");
}

function columns(tab: Tab, showNode: boolean, showHost: boolean): Column[] {
  const node: Column[] = showNode ? [{ label: "Node", width: "4.5rem" }] : [];
  const host: Column[] = showHost ? [HOST] : [];
  const time: Column = { label: "Time", width: "6.5rem" };
  switch (tab) {
    case "requests":
      return [
        time,
        ...node,
        ...host,
        { label: "Status", width: "3.5rem" },
        { label: "Method", width: "4rem" },
        { label: "Path · client", width: "minmax(0,1fr)" },
        { label: "Upstream", width: "8rem" },
        { label: "Took", width: "4rem" },
      ];
    case "streams":
      return [
        time,
        ...node,
        ...host,
        { label: "Client", width: "minmax(0,1fr)" },
        { label: "Proto", width: "3.5rem" },
        { label: "Status", width: "3.5rem" },
        { label: "In / out", width: "8rem" },
        { label: "Duration", width: "5rem" },
        { label: "Upstream", width: "8rem" },
      ];
    default:
      return [time, ...node, HOST, { label: "Message", width: "minmax(0,1fr)" }];
  }
}

const GRIDS = new Map<string, CSSProperties>();

/**
 * The grid for a tab, built once and then the same object: a row whose props
 * have not changed must not re-render, and a fresh style object each time
 * would be a changed prop.
 */
export function gridFor(tab: Tab, showNode: boolean, showHost: boolean): CSSProperties {
  const key = `${tab}:${showNode}:${showHost}`;
  let grid = GRIDS.get(key);
  if (!grid) {
    const widths = columns(tab, showNode, showHost).map((c) => c.width);
    grid = { gridTemplateColumns: widths.join(" ") };
    GRIDS.set(key, grid);
  }
  return grid;
}

/**
 * Where a truncated request or stream line starts: after the time, and after
 * the node and host cells when they show. Literal class names, so Tailwind
 * sees each one.
 */
const RAW_SPAN = ["col-[2/-1]", "col-[3/-1]", "col-[4/-1]"] as const;

/**
 * Every row is exactly this tall (px), so the page can render only the rows in
 * view and still know where each one sits.
 */
export const ROW_HEIGHT = 32;

const ROW_CLASS =
  "grid items-center gap-2 border-b px-2.5 py-1.5 text-left font-mono text-xs whitespace-nowrap";

export function LogHead({
  tab,
  showNode,
  showHost = false,
}: {
  tab: Tab;
  showNode: boolean;
  /** A Host column on Requests and Stream hosts (Errors and Security always have one). */
  showHost?: boolean;
}) {
  const host = hostColumn(tab, showHost);
  const cols = columns(tab, showNode, host);
  return (
    <div
      role="row"
      className={cn(ROW_CLASS, "bg-muted/40 text-[11px] tracking-wide text-muted-foreground uppercase")}
      style={gridFor(tab, showNode, host)}
    >
      {cols.map((c) => (
        <span key={c.label} role="columnheader">
          {c.label}
        </span>
      ))}
    </div>
  );
}

function seconds(value: string | undefined): string {
  const n = Number.parseFloat(value ?? "");
  if (!Number.isFinite(n)) return "—";
  return n < 1 ? `${Math.round(n * 1000)} ms` : `${n.toFixed(1)} s`;
}

function bytes(value: string | undefined): string {
  const n = Number.parseInt(value ?? "", 10);
  if (!Number.isFinite(n)) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function Client({ line }: { line: LiveLine }) {
  return (
    <>
      <span className="text-muted-foreground"> · {line.f?.ip ?? "—"} </span>
      <CountryFlag country={line.cc} />
    </>
  );
}

function cells(line: LiveLine, tab: Tab, hostLabel: string, showNode: boolean, showHost: boolean) {
  const f = line.f;
  if (line.truncated || !f) {
    const text = line.raw ?? "";
    if (tab === "requests" || tab === "streams") {
      // Everything after the time, and after the node and host cells when they show.
      const span = RAW_SPAN[Number(showNode) + Number(showHost)];
      return [
        <span key="raw" className={cn(span, "flex items-center gap-2 overflow-hidden")}>
          <Badge variant="warning">truncated</Badge>
          <span className="truncate">{text}</span>
        </span>,
      ];
    }
    return [
      <span key="host" className="truncate text-muted-foreground">
        {hostLabel}
      </span>,
      <span key="msg" className="flex items-center gap-2 overflow-hidden">
        {line.truncated ? <Badge variant="warning">truncated</Badge> : null}
        <span className="truncate">{text}</span>
      </span>,
    ];
  }
  if (line.log === "decision") {
    return [
      <span key="host" className="text-muted-foreground">
        <Badge variant="destructive">{f.type ?? "decision"}</Badge>
      </span>,
      <span key="msg" className="flex items-center gap-2 truncate">
        <span className="truncate">
          {f.scope} {f.value}
          <span className="text-muted-foreground">
            {" "}
            · {f.scenario || f.origin} · {f.duration}
          </span>
        </span>
        <CountryFlag country={line.cc} />
      </span>,
    ];
  }
  if (tab === "requests") {
    const cls = statusClass(f.st);
    return [
      <span key="st" className={cls ? STATUS_COLOUR[cls] : undefined}>
        {f.st}
      </span>,
      <span key="m">{f.m}</span>,
      <span key="p" className="truncate">
        {f.u}
        {f.cs === "denied" ? (
          <Badge variant="destructive" className="ml-1.5">
            CrowdSec
          </Badge>
        ) : null}
        <Client line={line} />
      </span>,
      <span key="up" className="truncate text-muted-foreground">
        {f.up || "—"}
      </span>,
      <span key="rt">{seconds(f.rt)}</span>,
    ];
  }
  if (tab === "streams") {
    return [
      <span key="c" className="truncate">
        {f.ip}
        <Client line={line} />
      </span>,
      <span key="pr">{f.pr}</span>,
      <span key="st">{f.st}</span>,
      <span key="io">
        {bytes(f.in)} / {bytes(f.out)}
      </span>,
      <span key="d">{seconds(f.dur)}</span>,
      <span key="up" className="truncate text-muted-foreground">
        {f.up || "—"}
      </span>,
    ];
  }
  return [
    <span key="host" className="truncate text-muted-foreground">
      {hostLabel}
    </span>,
    <span key="msg" className="truncate">
      {line.raw}
    </span>,
  ];
}

/**
 * One line. Memoised: a busy tab flushes ten times a second, and only the rows
 * that arrived (or whose selection changed) should render again, not all 1,000.
 * Every prop is a value or stable: `onSelect` takes the id, so the page passes
 * one callback for every row.
 */
export const LogRow = memo(function LogRow({
  line,
  tab,
  showNode,
  showHost = false,
  hostLabel,
  selected,
  onSelect,
}: {
  line: LiveLine;
  tab: Tab;
  showNode: boolean;
  /** A host cell on Requests and Stream hosts (Errors and Security always have one). */
  showHost?: boolean;
  hostLabel: string;
  selected: boolean;
  onSelect: (id: number) => void;
}) {
  const host = hostColumn(tab, showHost);
  return (
    <button
      type="button"
      onClick={() => onSelect(line.id)}
      aria-pressed={selected}
      className={cn(ROW_CLASS, "h-8 w-full py-0 hover:bg-muted/40", selected && "bg-muted/60")}
      style={gridFor(tab, showNode, host)}
    >
      <span>{lineTime(line)}</span>
      {showNode ? (
        <span className="rounded-md bg-muted px-1.5 text-center text-[11px]">{line.node}</span>
      ) : null}
      {host ? (
        <span className="truncate text-muted-foreground" title={hostLabel}>
          {hostLabel}
        </span>
      ) : null}
      {cells(line, tab, hostLabel, showNode, host)}
    </button>
  );
});

/** Every field of the selected line. */
export function LineDetail({ line, hostLabel }: { line: LiveLine; hostLabel: string }) {
  const entries: [string, string][] = [
    ["Host", hostLabel],
    ["Node", line.node],
    ...Object.entries(line.f ?? {}).map(([key, value]): [string, string] => [key, value]),
  ];
  if (line.raw !== null) entries.push(["Raw", line.raw]);
  if (line.cc) entries.push(["Country", line.cc]);
  return (
    <div className="rounded-xl border bg-muted/20 p-3 text-xs">
      <dl className="grid grid-cols-[8rem_minmax(0,1fr)] gap-x-3 gap-y-1">
        {entries.map(([key, value]) => (
          <div key={key} className="contents">
            <dt className="text-muted-foreground">{key}</dt>
            <dd className="font-mono break-all">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
