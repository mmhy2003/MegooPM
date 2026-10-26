"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type UIEvent } from "react";
import {
  Download,
  Pause,
  Play,
  SlidersHorizontal,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";

import { deadHosts, proxyHosts, redirectionHosts, streams } from "@/lib/api";
import {
  CARD_HEIGHT,
  LineDetail,
  LogCard,
  LogHead,
  LogRow,
  ROW_HEIGHT,
} from "@/components/live-logs/log-rows";
import { FiltersSheet, LineSheet, MoreMenu } from "@/components/live-logs/phone";
import {
  LINE_CAP,
  matches,
  TABS,
  toText,
  type LiveLine,
  type Tab,
} from "@/components/live-logs/lib";
import { useLiveLogs } from "@/components/live-logs/use-live-logs";
import { useIsMobile } from "@/hooks/use-mobile";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SearchInput } from "@/components/ui/search-input";
import { SearchableSelect } from "@/components/ui/searchable-select";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTab } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

const TAB_LABEL: Record<Tab, string> = {
  requests: "Requests",
  errors: "Errors",
  security: "Security",
  streams: "Stream hosts",
};

const FILTER_COUNT =
  "inline-flex size-4 items-center justify-center rounded-full bg-primary " +
  "text-[10px] font-semibold text-primary-foreground";

/** Short enough for all four to fit across a phone. */
const PHONE_TAB_LABEL: Record<Tab, string> = { ...TAB_LABEL, streams: "Streams" };

const STATUS_CLASSES = ["2xx", "3xx", "4xx", "5xx"] as const;

const UNCLAIMED = "u";
/** Every host's lines plus nginx's own: one channel, so new hosts show up on their own. */
const ALL_LOGS = "all";
/** nginx's own messages (reloads, config errors, workers): error lines, no host. */
const NGINX_ITSELF = "n";

interface HostOption {
  key: string;
  label: string;
}

/** First in the picker, before any host loads. */
const FIXED_OPTIONS: HostOption[] = [
  { key: ALL_LOGS, label: "All logs (every host + nginx itself)" },
  { key: NGINX_ITSELF, label: "nginx itself (no host)" },
];

// The themed Select has no empty value, so "every node" / "any status" is a
// value of its own that the filter state keeps as "".
const ANY_FILTER = "any";
const STATUS_ITEMS: Record<string, string> = {
  [ANY_FILTER]: "Status: any",
  ...Object.fromEntries(STATUS_CLASSES.map((c) => [c, c])),
};

/** Proxy, redirection, dead and stream hosts, keyed the way the socket names them. */
function useHostOptions() {
  const [options, setOptions] = useState<HostOption[]>(FIXED_OPTIONS);

  useEffect(() => {
    let active = true;
    void (async () => {
      const [proxy, redirect, dead, stream] = await Promise.allSettled([
        proxyHosts.list(),
        redirectionHosts.list(),
        deadHosts.list(),
        streams.list(),
      ]);
      if (!active) return;
      const out: HostOption[] = [...FIXED_OPTIONS];
      const names = (prefix: string, result: typeof proxy | typeof redirect | typeof dead) => {
        if (result.status !== "fulfilled") return;
        for (const host of result.value) {
          out.push({ key: `${prefix}${host.id}`, label: host.domain_names.join(", ") });
        }
      };
      names("p", proxy);
      names("r", redirect);
      names("d", dead);
      if (stream.status === "fulfilled") {
        for (const s of stream.value) {
          out.push({ key: `s${s.id}`, label: `Stream :${s.incoming_port}` });
        }
      }
      out.push({ key: UNCLAIMED, label: "Unclaimed (default site)" });
      setOptions(out);
    })();
    return () => {
      active = false;
    };
  }, []);

  return options;
}

/** Rows rendered beyond each edge of the view, so a fast scroll shows no gap. */
const OVERSCAN = 10;
/** The view's height before it has been measured (or where it cannot be: tests). */
const FALLBACK_VIEW_PX = 900;

/**
 * Which of `count` rows, each `rowHeight` px tall, are in view in the
 * scroller `ref` points at. A tab keeps 1,000 lines and a busy one changes
 * ten times a second: only the rows that can be seen are put in the page, so
 * a flush costs the same whether the tab holds 30 lines or 1,000.
 */
function useRowWindow(count: number, rowHeight: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewPx, setViewPx] = useState(0);

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const measure = () => setViewPx(el.clientHeight);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const onScroll = useCallback((event: UIEvent<HTMLDivElement>) => {
    setScrollTop(event.currentTarget.scrollTop);
  }, []);

  const inView = Math.ceil((viewPx || FALLBACK_VIEW_PX) / rowHeight);
  const start = Math.min(Math.max(0, Math.floor(scrollTop / rowHeight) - OVERSCAN), count);
  const end = Math.min(count, start + inView + 2 * OVERSCAN);
  return { ref, onScroll, start, end };
}

function Notice({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2.5 rounded-xl border border-warning/30 bg-warning/5 p-3 text-sm">
      <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" />
      <div>{children}</div>
    </div>
  );
}

function saveAsFile(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  // After the click has been handled: revoking at once can cancel the download.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function LiveLogsView() {
  const [tab, setTab] = useState<Tab>("requests");
  const [picked, setPicked] = useState<string[]>([]);
  const [node, setNode] = useState("");
  const [status, setStatus] = useState("");
  const [text, setText] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const isMobile = useIsMobile();

  const options = useHostOptions();
  const live = useLiveLogs({ hosts: picked, decisions: tab === "security" });

  // A chip names the choice as the picker does ("nginx itself (no host)"); a
  // row names its line's host ("nginx"). Lines that came through All logs
  // carry their real host key, and nginx's own carry `n`.
  const { chipLabelOf, labelOf } = useMemo(() => {
    const chips = new Map(options.map((o) => [o.key, o.label]));
    const hosts = new Map(chips).set(NGINX_ITSELF, "nginx");
    return {
      chipLabelOf: (key: string) => chips.get(key) ?? key,
      labelOf: (key: string) => hosts.get(key) ?? key,
    };
  }, [options]);

  // One node, "local": a node column would repeat the same word on every row.
  const showNode = useMemo(
    () => TABS.some((t) => live.lines[t].some((line) => line.node !== "local")),
    [live.lines],
  );
  const nodes = useMemo(
    () => [...new Set(TABS.flatMap((t) => live.lines[t].map((line) => line.node)))].sort(),
    [live.lines],
  );

  const rows = useMemo(() => {
    const filter = { node: showNode ? node : "", status: tab === "requests" ? status : "", text };
    // A tab keeps what arrived for earlier picks too. Unless All logs is on,
    // only the picked hosts' lines show, so a single host's view holds no
    // other host's lines without a Host column to tell them apart. Decisions
    // belong to no host.
    const shown = picked.includes(ALL_LOGS) ? null : new Set(picked);
    const picks = (line: LiveLine) => !shown || line.log === "decision" || shown.has(line.host);
    return live.lines[tab].filter((line) => picks(line) && matches(line, filter)).reverse();
  }, [live.lines, tab, node, status, text, showNode, picked]);

  const selected = selectedId === null ? null : rows.find((line) => line.id === selectedId);
  const rowHeight = isMobile ? CARD_HEIGHT : ROW_HEIGHT;
  const { ref: rowsRef, onScroll, start, end } = useRowWindow(rows.length, rowHeight);
  // One callback for every row, so a memoised row re-renders only when its own
  // props change.
  const select = useCallback((id: number) => setSelectedId((cur) => (cur === id ? null : id)), []);

  // Stream hosts belong to the Stream tab, everything else to the others; All
  // logs covers every tab. Unclaimed is the default servers (http only), and
  // nginx itself sends error lines only: neither is on the Stream tab.
  const isStream = (key: string) => key.startsWith("s");
  const onTab = (key: string) => key === ALL_LOGS || isStream(key) === (tab === "streams");
  const visiblePicked = picked.filter(onTab);
  const addable = options.filter((o) => !picked.includes(o.key) && onTab(o.key));
  // A Host column on Requests and Stream hosts whenever this tab can mix hosts;
  // with one host picked, it would repeat the same name on every row.
  const showHost = visiblePicked.includes(ALL_LOGS) || visiblePicked.length > 1;
  // All logs is exclusive: it already carries every host's lines, and a host
  // watched beside it would show each of its lines twice.
  const pick = (key: string) =>
    setPicked((prev) => {
      if (key === ALL_LOGS) return [ALL_LOGS];
      if (prev.includes(key)) return prev;
      return [...prev.filter((k) => k !== ALL_LOGS), key];
    });
  // Only the chips on screen: a stream host picked on the Stream tab stays.
  const clearShown = () => setPicked((prev) => prev.filter((k) => !onTab(k)));

  // nginx itself sends error lines only: on any other tab, nothing will come.
  const emptyText =
    picked.length === 0
      ? "Pick a host to start watching."
      : picked.length === 1 && picked[0] === NGINX_ITSELF && tab !== "errors"
        ? "nginx's own messages show on the Errors tab."
        : tab === "security" && text === ""
          ? "Waiting for security events…"
          : "Waiting for traffic…";

  const connection =
    live.status === "live"
      ? `Live · ${live.rate} req/s`
      : live.status === "connecting"
        ? "Connecting…"
        : "Reconnecting…";

  const pending =
    live.paused && live.pending > 0 ? (
      <span className="text-xs text-muted-foreground">
        {live.pending > LINE_CAP
          ? `${LINE_CAP.toLocaleString()}+ new`
          : `${live.pending.toLocaleString()} new`}
      </span>
    ) : null;

  const liveStatus = (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 text-xs",
        live.status === "live" ? "text-success" : "text-muted-foreground",
      )}
    >
      <span
        className={cn(
          "size-2 rounded-full",
          live.status === "live" ? "bg-success" : "bg-muted-foreground",
        )}
      />
      {connection}
    </span>
  );

  // Keyed by tab: the picker's own filter text belongs to the list it filtered.
  const hostPicker = (
    <SearchableSelect
      key={tab}
      aria-label="Add host"
      value=""
      items={Object.fromEntries(addable.map((o) => [o.key, o.label]))}
      onValueChange={(key) => {
        if (key) pick(key);
      }}
      placeholder="+ Add host"
      searchLabel="Search hosts"
      disabled={addable.length === 0}
      className={isMobile ? "w-full" : "w-56"}
    />
  );

  const textFilter = (
    <SearchInput
      value={text}
      onValueChange={setText}
      label="Filter lines"
      placeholder="Filter: path, IP, user agent…"
      className={isMobile ? "min-w-0 flex-1" : "sm:max-w-64"}
    />
  );

  const chips = visiblePicked.map((key) => (
    <Badge key={key} variant="outline" className="h-7 shrink-0 gap-1.5 pr-1">
      <span>{chipLabelOf(key)}</span>
      <button
        type="button"
        aria-label={`Stop watching ${chipLabelOf(key)}`}
        onClick={() => setPicked((prev) => prev.filter((k) => k !== key))}
        className="rounded p-0.5 hover:bg-muted"
      >
        <X className="size-3" />
      </button>
    </Badge>
  ));

  const save = () => saveAsFile(`live-logs-${tab}.txt`, toText([...rows].reverse()));

  // The filters a phone moves into its panel, each only where it applies.
  const statusApplies = tab === "requests";
  const filtersOn = (statusApplies && status ? 1 : 0) + (showNode && node ? 1 : 0);

  return (
    <div className="flex flex-col gap-3">
      {live.status === "revoked" ? (
        <Notice>
          <p className="font-medium">Your access was revoked</p>
          <p className="text-muted-foreground">Sign in again to watch live logs.</p>
        </Notice>
      ) : null}
      {live.status === "error" ? (
        <Notice>
          <p className="font-medium">The live connection was refused</p>
          <p className="text-muted-foreground">
            The server did not understand what this page asked it to watch. Reload the page.
          </p>
        </Notice>
      ) : null}
      {live.skipped > 0 ? (
        <Notice>{live.skipped.toLocaleString()} lines skipped — too many to show</Notice>
      ) : null}

      <Tabs
        value={tab}
        onValueChange={(next) => {
          setTab(next as Tab);
          setSelectedId(null);
        }}
      >
        <TabsList className={isMobile ? "w-full" : undefined}>
          {TABS.map((t) => (
            <TabsTab key={t} value={t} className={isMobile ? "px-2" : undefined}>
              {isMobile ? PHONE_TAB_LABEL[t] : TAB_LABEL[t]}
            </TabsTab>
          ))}
        </TabsList>
      </Tabs>

      {isMobile ? (
        <>
          {hostPicker}
          {chips.length > 0 ? (
            // One row that slides sideways: on a phone, wrapping chips would
            // push the lines off the first screen.
            <div data-testid="picked" className="-mx-1 flex items-center gap-2 overflow-x-auto px-1">
              {chips}
            </div>
          ) : null}
          <div className="flex items-center gap-2">
            {textFilter}
            {statusApplies || showNode ? (
              <Button
                variant="outline"
                size="sm"
                aria-label={filtersOn ? `Filters, ${filtersOn} on` : "Filters"}
                onClick={() => setFiltersOpen(true)}
              >
                <SlidersHorizontal />
                Filters
                {filtersOn ? (
                  <span className={FILTER_COUNT}>
                    {filtersOn}
                  </span>
                ) : null}
              </Button>
            ) : null}
            <Button
              variant="outline"
              size="icon-sm"
              aria-label={live.paused ? "Resume" : "Pause"}
              onClick={() => live.setPaused(!live.paused)}
            >
              {live.paused ? <Play /> : <Pause />}
            </Button>
            <MoreMenu onSave={save} canSave={rows.length > 0} onClear={live.clear} />
          </div>
          <div className="flex items-center justify-between gap-2">
            {liveStatus}
            {pending}
          </div>
          <FiltersSheet
            open={filtersOpen}
            onOpenChange={setFiltersOpen}
            status={status}
            statuses={statusApplies ? STATUS_CLASSES : null}
            onStatus={setStatus}
            node={node}
            nodes={showNode ? nodes : null}
            onNode={setNode}
          />
        </>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            {hostPicker}
            {showNode ? (
              <Select
                value={node || ANY_FILTER}
                onValueChange={(v) => setNode(!v || v === ANY_FILTER ? "" : String(v))}
                items={{ [ANY_FILTER]: "All nodes", ...Object.fromEntries(nodes.map((n) => [n, n])) }}
              >
                <SelectTrigger aria-label="Node filter" className="w-40">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ANY_FILTER}>All nodes</SelectItem>
                  {nodes.map((n) => (
                    <SelectItem key={n} value={n}>
                      {n}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : null}
            {statusApplies ? (
              <Select
                value={status || ANY_FILTER}
                onValueChange={(v) => setStatus(!v || v === ANY_FILTER ? "" : String(v))}
                items={STATUS_ITEMS}
              >
                <SelectTrigger aria-label="Status filter" className="w-36">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(STATUS_ITEMS).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : null}
            {textFilter}
            {/* Pinned to the end of the line, so they keep their corner. */}
            <span className="ml-auto flex items-center gap-2">
              {pending}
              {liveStatus}
              <Button variant="outline" size="sm" onClick={() => live.setPaused(!live.paused)}>
                {live.paused ? <Play /> : <Pause />}
                {live.paused ? "Resume" : "Pause"}
              </Button>
            </span>
          </div>
          {chips.length > 0 ? (
            // Their own line, below the controls: picking a host never moves them.
            <div data-testid="picked" className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted-foreground">Watching</span>
              {chips}
              {chips.length > 1 ? (
                <Button variant="ghost" size="xs" onClick={clearShown}>
                  Clear all
                </Button>
              ) : null}
            </div>
          ) : null}
        </>
      )}

      {tab === "streams" ? (
        <p className="text-sm text-muted-foreground">Connections appear here when they close.</p>
      ) : null}

      {isMobile ? (
        <section aria-label={TAB_LABEL[tab]} className="overflow-hidden rounded-xl border">
          <div ref={rowsRef} onScroll={onScroll} data-testid="rows" className="max-h-[65vh] overflow-auto">
            {rows.length === 0 ? (
              <p className="px-3 py-10 text-center text-sm text-muted-foreground">{emptyText}</p>
            ) : (
              <div style={{ height: rows.length * CARD_HEIGHT }}>
                <div style={{ transform: `translateY(${start * CARD_HEIGHT}px)` }}>
                  {rows.slice(start, end).map((line) => (
                    <LogCard
                      key={line.id}
                      line={line}
                      tab={tab}
                      showNode={showNode}
                      hostLabel={labelOf(line.host)}
                      selected={line.id === selectedId}
                      onSelect={select}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>
        </section>
      ) : (
        <div role="table" aria-label={TAB_LABEL[tab]} className="overflow-hidden rounded-xl border">
          <LogHead tab={tab} showNode={showNode} showHost={showHost} />
          <div ref={rowsRef} onScroll={onScroll} data-testid="rows" className="max-h-[60vh] overflow-auto">
            {rows.length === 0 ? (
              <p className="px-3 py-10 text-center text-sm text-muted-foreground">
                {emptyText}
              </p>
            ) : (
              // Full height, so the scrollbar is the whole tab's; only the rows in
              // view are in it, moved to where they sit.
              <div style={{ height: rows.length * ROW_HEIGHT }}>
                <div style={{ transform: `translateY(${start * ROW_HEIGHT}px)` }}>
                  {rows.slice(start, end).map((line) => (
                    <LogRow
                      key={line.id}
                      line={line}
                      tab={tab}
                      showNode={showNode}
                      showHost={showHost}
                      hostLabel={labelOf(line.host)}
                      selected={line.id === selectedId}
                      onSelect={select}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {isMobile ? (
        <LineSheet
          line={selected ?? null}
          hostLabel={selected ? labelOf(selected.host) : ""}
          onClose={() => setSelectedId(null)}
        />
      ) : selected ? (
        <LineDetail line={selected} hostLabel={labelOf(selected.host)} />
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>
          Showing the last 1,000 lines in this tab only.{" "}
          {isMobile ? "Tap a line for every field." : "Click a line for every field."}
        </span>
        {isMobile ? null : (
          <span className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={save} disabled={rows.length === 0}>
              <Download /> Save what&apos;s on screen
            </Button>
            <Button variant="ghost" size="sm" onClick={live.clear}>
              <Trash2 /> Clear
            </Button>
          </span>
        )}
      </div>
    </div>
  );
}
