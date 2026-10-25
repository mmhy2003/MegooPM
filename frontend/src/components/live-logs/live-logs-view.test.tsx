import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { LiveLogsView } from "@/components/live-logs/live-logs-view";
import type { LiveLine, Tab } from "@/components/live-logs/lib";

const hook = vi.hoisted(() => ({ state: {} as Record<string, unknown>, calls: [] as unknown[] }));
vi.mock("@/components/live-logs/use-live-logs", () => ({
  useLiveLogs: (watch: unknown) => {
    hook.calls.push(watch);
    return hook.state;
  },
}));

vi.mock("@/lib/api", () => ({
  proxyHosts: { list: async () => [{ id: 1, domain_names: ["cloud.example.com"] }] },
  redirectionHosts: { list: async () => [{ id: 2, domain_names: ["old.example.com"] }] },
  deadHosts: { list: async () => [] },
  streams: { list: async () => [{ id: 3, incoming_port: 2222 }] },
}));

let nextId = 1;
function line(over: Partial<LiveLine> = {}): LiveLine {
  return {
    id: nextId++,
    at: Date.UTC(2026, 9, 25, 10, 42, 17),
    node: "local",
    host: "p1",
    log: "request",
    sev: 6,
    f: { ts: "1790000000.204", st: "200", m: "GET", u: "/apps/files/", ip: "5.6.7.8", ua: "curl" },
    raw: null,
    truncated: false,
    cc: null,
    ...over,
  };
}

function setState(over: { lines?: Partial<Record<Tab, LiveLine[]>> } & Record<string, unknown>) {
  const { lines, ...rest } = over;
  hook.state = {
    status: "live",
    lines: { requests: [], errors: [], security: [], streams: [], ...lines },
    skipped: 0,
    pending: 0,
    rate: 0,
    paused: false,
    setPaused: vi.fn(),
    clear: vi.fn(),
    ...rest,
  };
}

beforeEach(() => {
  nextId = 1;
  hook.calls = [];
  setState({});
});
afterEach(cleanup);

describe("LiveLogsView", () => {
  it("renders an attacker's user agent as literal text", async () => {
    const ua = "<img src=x onerror=alert(1)>";
    setState({ lines: { requests: [line({ f: { st: "200", m: "GET", u: "/", ip: "1.1.1.1", ua } })] } });
    const { container } = render(<LiveLogsView />);
    await userEvent.click(screen.getByRole("button", { name: /GET/ }));
    expect(screen.getByText(ua)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(document.body.querySelector("img")).toBeNull();
  });

  it("says how many lines were skipped", () => {
    setState({ skipped: 120 });
    render(<LiveLogsView />);
    expect(screen.getByText(/120 lines skipped — too many to show/)).toBeInTheDocument();
  });

  it("shows a truncated line raw with its badge", async () => {
    setState({
      lines: {
        requests: [line({ f: null, raw: '{"ts":"1790000000.1","u":"/aaaa', truncated: true })],
      },
    });
    render(<LiveLogsView />);
    expect(screen.getByText('{"ts":"1790000000.1","u":"/aaaa')).toBeInTheDocument();
    expect(screen.getByText("truncated")).toBeInTheDocument();
  });

  it("hides the node column when every line is from local", () => {
    setState({ lines: { requests: [line(), line()] } });
    render(<LiveLogsView />);
    expect(screen.queryByRole("columnheader", { name: "Node" })).toBeNull();
    expect(screen.queryByLabelText("Node")).toBeNull();
  });

  it("shows the node column when another node reported", () => {
    setState({ lines: { requests: [line(), line({ node: "node-b" })] } });
    render(<LiveLogsView />);
    expect(screen.getByRole("columnheader", { name: "Node" })).toBeInTheDocument();
    expect(screen.getAllByText("node-b").length).toBeGreaterThan(1);
  });

  it("tags a CrowdSec-denied request", () => {
    setState({
      lines: { requests: [line({ f: { st: "403", m: "GET", u: "/x", ip: "1.1.1.1", cs: "denied" } })] },
    });
    render(<LiveLogsView />);
    expect(screen.getByText("CrowdSec")).toBeInTheDocument();
  });

  it("explains that stream connections appear when they close", async () => {
    render(<LiveLogsView />);
    expect(screen.queryByText("Connections appear here when they close.")).toBeNull();
    await userEvent.click(screen.getByRole("tab", { name: "Stream hosts" }));
    expect(screen.getByText("Connections appear here when they close.")).toBeInTheDocument();
  });

  it("holds rendering on Pause and shows how many are new", async () => {
    const setPaused = vi.fn();
    setState({ paused: true, pending: 5, setPaused });
    render(<LiveLogsView />);
    expect(screen.getByText("5 new")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /resume/i }));
    expect(setPaused).toHaveBeenCalledWith(false);
  });

  it("watches the hosts picked, and decisions only on the Security tab", async () => {
    render(<LiveLogsView />);
    await userEvent.selectOptions(await screen.findByLabelText("Add host"), "p1");
    expect(hook.calls.at(-1)).toEqual({ hosts: ["p1"], decisions: false });
    expect(within(screen.getByTestId("picked")).getByText("cloud.example.com")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Security" }));
    expect(hook.calls.at(-1)).toEqual({ hosts: ["p1"], decisions: true });
  });

  it("offers stream hosts only on the Stream tab, and the default site always", async () => {
    render(<LiveLogsView />);
    const add = await screen.findByLabelText("Add host");
    expect(within(add).getByRole("option", { name: "Unclaimed (default site)" })).toBeInTheDocument();
    expect(within(add).queryByRole("option", { name: /2222/ })).toBeNull();
    await userEvent.click(screen.getByRole("tab", { name: "Stream hosts" }));
    expect(within(screen.getByLabelText("Add host")).getByRole("option", { name: /2222/ })).toBeInTheDocument();
  });

  it("says when access was revoked", () => {
    setState({ status: "revoked" });
    render(<LiveLogsView />);
    expect(screen.getByText(/access was revoked/i)).toBeInTheDocument();
  });
});
