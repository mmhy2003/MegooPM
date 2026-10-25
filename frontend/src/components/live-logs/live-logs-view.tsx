"use client";

import { useEffect, useMemo, useState } from "react";
import { Download, Pause, Play, Trash2, TriangleAlert, X } from "lucide-react";

import { deadHosts, proxyHosts, redirectionHosts, streams } from "@/lib/api";
import { LineDetail, LogHead, LogRow } from "@/components/live-logs/log-rows";
import { matches, TABS, toText, type Tab } from "@/components/live-logs/lib";
import { useLiveLogs } from "@/components/live-logs/use-live-logs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SearchInput } from "@/components/ui/search-input";
import { Tabs, TabsList, TabsTab } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

const TAB_LABEL: Record<Tab, string> = {
  requests: "Requests",
  errors: "Errors",
  security: "Security",
  streams: "Stream hosts",
};

const STATUS_CLASSES = ["2xx", "3xx", "4xx", "5xx"] as const;

const UNCLAIMED = "u";

interface HostOption {
  key: string;
  label: string;
}

const SELECT_CLASS =
  "h-8 rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30";

/** Proxy, redirection, dead and stream hosts, keyed the way the socket names them. */
function useHostOptions() {
  const [options, setOptions] = useState<HostOption[]>([]);

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
      const out: HostOption[] = [];
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
  URL.revokeObjectURL(url);
}

export function LiveLogsView() {
  const [tab, setTab] = useState<Tab>("requests");
  const [picked, setPicked] = useState<string[]>([]);
  const [node, setNode] = useState("");
  const [status, setStatus] = useState("");
  const [text, setText] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const options = useHostOptions();
  const live = useLiveLogs({ hosts: picked, decisions: tab === "security" });

  const labelOf = useMemo(() => {
    const map = new Map(options.map((o) => [o.key, o.label]));
    return (key: string) => map.get(key) ?? key;
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
    return live.lines[tab].filter((line) => matches(line, filter)).reverse();
  }, [live.lines, tab, node, status, text, showNode]);

  const selected = selectedId === null ? null : rows.find((line) => line.id === selectedId);

  // Stream hosts belong to the Stream tab, everything else to the others.
  const isStream = (key: string) => key.startsWith("s");
  const visiblePicked = picked.filter((key) => isStream(key) === (tab === "streams"));
  const addable = options.filter(
    (o) => !picked.includes(o.key) && (o.key === UNCLAIMED || isStream(o.key) === (tab === "streams")),
  );

  const connection =
    live.status === "live"
      ? `Live · ${live.rate} req/s`
      : live.status === "connecting"
        ? "Connecting…"
        : "Reconnecting…";

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
        <TabsList>
          {TABS.map((t) => (
            <TabsTab key={t} value={t}>
              {TAB_LABEL[t]}
            </TabsTab>
          ))}
        </TabsList>
      </Tabs>

      <div className="flex flex-wrap items-center gap-2">
        <div data-testid="picked" className="flex flex-wrap items-center gap-2">
          {visiblePicked.map((key) => (
            <Badge key={key} variant="outline" className="h-7 gap-1.5 pr-1">
              <span>{labelOf(key)}</span>
              <button
                type="button"
                aria-label={`Stop watching ${labelOf(key)}`}
                onClick={() => setPicked((prev) => prev.filter((k) => k !== key))}
                className="rounded p-0.5 hover:bg-muted"
              >
                <X className="size-3" />
              </button>
            </Badge>
          ))}
        </div>
        <select
          aria-label="Add host"
          className={SELECT_CLASS}
          value=""
          onChange={(e) => {
            if (e.target.value) setPicked((prev) => [...prev, e.target.value]);
          }}
        >
          <option value="">+ host</option>
          {addable.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>
        {showNode ? (
          <select
            aria-label="Node filter"
            className={SELECT_CLASS}
            value={node}
            onChange={(e) => setNode(e.target.value)}
          >
            <option value="">All nodes</option>
            {nodes.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        ) : null}
        {tab === "requests" ? (
          <select
            aria-label="Status filter"
            className={SELECT_CLASS}
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="">Status: any</option>
            {STATUS_CLASSES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        ) : null}
        <SearchInput
          value={text}
          onValueChange={setText}
          label="Filter lines"
          placeholder="Filter: path, IP, user agent…"
          className="sm:max-w-64"
        />
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
        <Button variant="outline" size="sm" onClick={() => live.setPaused(!live.paused)}>
          {live.paused ? <Play /> : <Pause />}
          {live.paused ? "Resume" : "Pause"}
        </Button>
        {live.paused && live.pending > 0 ? (
          <span className="text-xs text-muted-foreground">{live.pending} new</span>
        ) : null}
      </div>

      {tab === "streams" ? (
        <p className="text-sm text-muted-foreground">Connections appear here when they close.</p>
      ) : null}

      <div role="table" aria-label={TAB_LABEL[tab]} className="overflow-hidden rounded-xl border">
        <LogHead tab={tab} showNode={showNode} />
        <div className="max-h-[60vh] overflow-auto">
          {rows.length === 0 ? (
            <p className="px-3 py-10 text-center text-sm text-muted-foreground">
              {picked.length === 0
                ? "Pick a host to start watching."
                : tab === "security" && text === ""
                  ? "Waiting for security events…"
                  : "Waiting for traffic…"}
            </p>
          ) : (
            rows.map((line) => (
              <LogRow
                key={line.id}
                line={line}
                tab={tab}
                showNode={showNode}
                hostLabel={labelOf(line.host)}
                selected={line.id === selectedId}
                onSelect={() => setSelectedId(line.id === selectedId ? null : line.id)}
              />
            ))
          )}
        </div>
      </div>

      {selected ? <LineDetail line={selected} hostLabel={labelOf(selected.host)} /> : null}

      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>Showing the last 1,000 lines in this tab only. Click a line for every field.</span>
        <span className="flex gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => saveAsFile(`live-logs-${tab}.txt`, toText([...rows].reverse()))}
            disabled={rows.length === 0}
          >
            <Download /> Save what&apos;s on screen
          </Button>
          <Button variant="ghost" size="sm" onClick={live.clear}>
            <Trash2 /> Clear
          </Button>
        </span>
      </div>
    </div>
  );
}
