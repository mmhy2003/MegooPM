import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { LiveLogsView } from "@/components/live-logs/live-logs-view";
import type { LiveLine, Tab } from "@/components/live-logs/lib";
import { ROW_HEIGHT } from "@/components/live-logs/log-rows";

const hook = vi.hoisted(() => ({ state: {} as Record<string, unknown>, calls: [] as unknown[] }));
vi.mock("@/components/live-logs/use-live-logs", () => ({
  useLiveLogs: (watch: unknown) => {
    hook.calls.push(watch);
    return hook.state;
  },
}));

// Counts row renders: every row shows its time, and nothing else asks for it.
const rendered = vi.hoisted(() => ({ rows: 0 }));
vi.mock("@/components/live-logs/lib", async (importOriginal) => {
  const lib = await importOriginal<typeof import("@/components/live-logs/lib")>();
  return {
    ...lib,
    lineTime: (l: LiveLine) => {
      rendered.rows += 1;
      return lib.lineTime(l);
    },
  };
});

// Counts finished host-list loads, so a test can wait for the picker's data.
const loads = vi.hoisted(() => ({ done: 0 }));
vi.mock("@/lib/api", () => {
  const done = <T,>(rows: T[]) => {
    loads.done += 1;
    return rows;
  };
  return {
    proxyHosts: { list: async () => done([{ id: 1, domain_names: ["cloud.example.com"] }]) },
    redirectionHosts: { list: async () => done([{ id: 2, domain_names: ["old.example.com"] }]) },
    deadHosts: { list: async () => done([]) },
    streams: { list: async () => done([{ id: 3, incoming_port: 2222 }]) },
  };
});

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
    expect(within(screen.getByRole("table")).getByText("node-b")).toBeInTheDocument();
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

  /** Opens the host picker and waits for the host list to have loaded. */
  async function openHostPicker(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole("combobox", { name: "Add host" }));
    return screen.findByRole("listbox");
  }

  it("watches the hosts picked, and decisions only on the Security tab", async () => {
    const user = userEvent.setup();
    render(<LiveLogsView />);
    await openHostPicker(user);
    await user.click(await screen.findByRole("option", { name: "cloud.example.com" }));
    expect(hook.calls.at(-1)).toEqual({ hosts: ["p1"], decisions: false });
    expect(within(screen.getByTestId("picked")).getByText("cloud.example.com")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Security" }));
    expect(hook.calls.at(-1)).toEqual({ hosts: ["p1"], decisions: true });
  });

  it("finds a host by typing part of its name", async () => {
    const user = userEvent.setup();
    render(<LiveLogsView />);
    await openHostPicker(user);
    await user.type(screen.getByLabelText("Search hosts"), "OLD");
    expect(await screen.findByRole("option", { name: "old.example.com" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "cloud.example.com" })).toBeNull();
  });

  it("offers stream hosts only on the Stream tab, and the default site everywhere else", async () => {
    const user = userEvent.setup();
    render(<LiveLogsView />);
    await openHostPicker(user);
    const unclaimed = await screen.findByRole("option", { name: "Unclaimed (default site)" });
    expect(unclaimed).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /2222/ })).toBeNull();
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("tab", { name: "Stream hosts" }));
    await openHostPicker(user);
    expect(await screen.findByRole("option", { name: /2222/ })).toBeInTheDocument();
    // The default servers carry no stream lines; its chip would not even show here.
    expect(screen.queryByRole("option", { name: "Unclaimed (default site)" })).toBeNull();
  });

  const ALL_LOGS = "All logs (every host + nginx itself)";
  const NGINX_ITSELF = "nginx itself (no host)";

  it("offers All logs first, then nginx itself", async () => {
    const user = userEvent.setup();
    render(<LiveLogsView />);
    await openHostPicker(user);
    await screen.findByRole("option", { name: "cloud.example.com" });
    const names = screen.getAllByRole("option").map((o) => o.textContent);
    expect(names.slice(0, 2)).toEqual([ALL_LOGS, NGINX_ITSELF]);
  });

  it("makes All logs replace the picked hosts, and a host picked after it replace it", async () => {
    const user = userEvent.setup();
    render(<LiveLogsView />);
    await openHostPicker(user);
    await user.click(await screen.findByRole("option", { name: "cloud.example.com" }));
    await openHostPicker(user);
    await user.click(await screen.findByRole("option", { name: NGINX_ITSELF }));
    expect(hook.calls.at(-1)).toEqual({ hosts: ["p1", "n"], decisions: false });

    await openHostPicker(user);
    await user.click(await screen.findByRole("option", { name: ALL_LOGS }));
    expect(hook.calls.at(-1)).toEqual({ hosts: ["all"], decisions: false });
    const picked = within(screen.getByTestId("picked"));
    expect(picked.getByText(ALL_LOGS)).toBeInTheDocument();
    expect(picked.queryByText("cloud.example.com")).toBeNull();

    await openHostPicker(user);
    await user.click(await screen.findByRole("option", { name: "old.example.com" }));
    expect(hook.calls.at(-1)).toEqual({ hosts: ["r2"], decisions: false });
    expect(picked.queryByText(ALL_LOGS)).toBeNull();
  });

  it("offers All logs on the Stream tab too, and nginx itself only on the others", async () => {
    const user = userEvent.setup();
    render(<LiveLogsView />);
    for (const tab of ["Errors", "Security", "Requests"]) {
      await user.click(screen.getByRole("tab", { name: tab }));
      await openHostPicker(user);
      expect(await screen.findByRole("option", { name: NGINX_ITSELF })).toBeInTheDocument();
      await user.keyboard("{Escape}");
    }
    await user.click(screen.getByRole("tab", { name: "Stream hosts" }));
    await openHostPicker(user);
    await user.click(await screen.findByRole("option", { name: ALL_LOGS }));
    // Picked on the Stream tab, and still shown there: it covers stream hosts too.
    expect(hook.calls.at(-1)).toEqual({ hosts: ["all"], decisions: false });
    expect(within(screen.getByTestId("picked")).getByText(ALL_LOGS)).toBeInTheDocument();
    await openHostPicker(user);
    await screen.findByRole("option", { name: /2222/ });
    expect(screen.queryByRole("option", { name: NGINX_ITSELF })).toBeNull();
  });

  it("labels nginx's own lines nginx, and a line through All logs with its real host", async () => {
    const user = userEvent.setup();
    setState({
      lines: {
        errors: [
          line({ host: "n", log: "error", sev: 5, f: null, raw: "signal process started" }),
          line({ host: "p1", log: "error", sev: 3, f: null, raw: "connect() failed" }),
        ],
      },
    });
    render(<LiveLogsView />);
    await user.click(screen.getByRole("tab", { name: "Errors" }));
    const table = within(screen.getByRole("table"));
    expect(table.getByText("nginx")).toBeInTheDocument();
    expect(await table.findByText("cloud.example.com")).toBeInTheDocument();
  });

  /** Picks each option, by name, from the host picker. */
  async function pickHosts(user: ReturnType<typeof userEvent.setup>, ...names: string[]) {
    for (const name of names) {
      await openHostPicker(user);
      await user.click(await screen.findByRole("option", { name }));
    }
  }

  const hostHeader = () => screen.queryByRole("columnheader", { name: "Host" });

  it("shows a Host column on Requests when the view can mix hosts, and not for one host", async () => {
    const user = userEvent.setup();
    setState({ lines: { requests: [line({ host: "p1" }), line({ host: "u" })] } });
    render(<LiveLogsView />);
    expect(hostHeader()).toBeNull();
    await pickHosts(user, "cloud.example.com");
    expect(hostHeader()).toBeNull();

    await pickHosts(user, "old.example.com");
    expect(hostHeader()).toBeInTheDocument();
    const table = within(screen.getByRole("table"));
    const cell = table.getByText("cloud.example.com");
    expect(cell).toHaveAttribute("title", "cloud.example.com");
    expect(cell.className).toContain("truncate");
    expect(table.getByText("Unclaimed (default site)")).toBeInTheDocument();

    await user.click(within(screen.getByTestId("picked")).getByRole("button", { name: /old\.example/ }));
    expect(hostHeader()).toBeNull();

    await pickHosts(user, ALL_LOGS);
    expect(hostHeader()).toBeInTheDocument();
  });

  it("shows a Host column on Stream hosts under All logs", async () => {
    const user = userEvent.setup();
    setState({
      lines: {
        streams: [line({ host: "s3", log: "stream", f: { ip: "1.1.1.1", pr: "TCP", st: "200" } })],
      },
    });
    render(<LiveLogsView />);
    await user.click(screen.getByRole("tab", { name: "Stream hosts" }));
    expect(hostHeader()).toBeNull();
    await pickHosts(user, ALL_LOGS);
    expect(hostHeader()).toBeInTheDocument();
    expect(await within(screen.getByRole("table")).findByText("Stream :2222")).toBeInTheDocument();
  });

  it("starts a truncated line after the node and host cells when both show", async () => {
    const user = userEvent.setup();
    const raw = '{"k":"p1","ts":"1790000000.1","u":"/aaaa';
    setState({
      lines: { requests: [line({ node: "node-b", f: null, raw, truncated: true })] },
    });
    render(<LiveLogsView />);
    await pickHosts(user, ALL_LOGS);
    const cell = screen.getByText(raw).parentElement;
    expect(cell?.className).toContain("col-[4/-1]");
    expect(within(screen.getByRole("table")).getByText("cloud.example.com")).toBeInTheDocument();
  });

  it("uses the themed dropdowns, never the browser's own select", () => {
    setState({ lines: { requests: [line({ node: "node-a" }), line({ node: "node-b" })] } });
    const { container } = render(<LiveLogsView />);
    // A native <select> ignores the app theme: its open list is drawn by the OS.
    expect(container.querySelector("select")).toBeNull();
    expect(screen.getByRole("combobox", { name: "Node filter" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Status filter" })).toBeInTheDocument();
  });

  it("filters requests by status class from the themed dropdown", async () => {
    const user = userEvent.setup();
    setState({
      lines: {
        requests: [
          line({ f: { st: "200", m: "GET", u: "/fine" } }),
          line({ f: { st: "502", m: "GET", u: "/broken" } }),
        ],
      },
    });
    render(<LiveLogsView />);
    await user.click(screen.getByRole("combobox", { name: "Status filter" }));
    await user.click(await screen.findByRole("option", { name: "5xx" }));
    expect(screen.getByText("/broken")).toBeInTheDocument();
    expect(screen.queryByText("/fine")).toBeNull();
  });

  it("renders only the new row when a line arrives, and only the clicked one on select", async () => {
    const first = [line(), line(), line()];
    setState({ lines: { requests: first } });
    loads.done = 0;
    const { rerender } = render(<LiveLogsView />);
    // The host labels load once; rows re-render for that, and only that.
    await waitFor(() => expect(loads.done).toBe(4));
    await act(async () => {});

    rendered.rows = 0;
    setState({ lines: { requests: [...first, line({ f: { st: "200", m: "GET", u: "/new" } })] } });
    rerender(<LiveLogsView />);
    expect(rendered.rows).toBe(1);

    rendered.rows = 0;
    fireEvent.click(screen.getByRole("button", { name: /\/new/ }));
    expect(rendered.rows).toBe(1);
  });

  it("puts only the rows in view in the page, and follows the scroll", () => {
    const many = Array.from({ length: 1000 }, (_, i) =>
      line({ f: { st: "200", m: "GET", u: `/n${i}`, ip: "1.1.1.1" } }),
    );
    setState({ lines: { requests: many } });
    render(<LiveLogsView />);
    const table = screen.getByRole("table");
    const rows = () => within(table).getAllByRole("button");
    // Newest first: /n999 at the top. A tab holds 1,000 lines; the page
    // renders a window of them, not all.
    expect(rows().length).toBeLessThan(100);
    expect(within(table).getByText("/n999")).toBeInTheDocument();
    expect(within(table).queryByText("/n0")).toBeNull();

    const scroller = screen.getByTestId("rows");
    fireEvent.scroll(scroller, { target: { scrollTop: ROW_HEIGHT * 990 } });
    expect(within(table).getByText("/n0")).toBeInTheDocument();
    expect(within(table).queryByText("/n999")).toBeNull();
    expect(rows().length).toBeLessThan(100);
  });

  it("starts a truncated line after the node cell when the node column shows", () => {
    const raw = '{"k":"p1","ts":"1790000000.1","u":"/aaaa';
    setState({
      lines: { requests: [line({ node: "node-b", f: null, raw, truncated: true })] },
    });
    render(<LiveLogsView />);
    const cell = screen.getByText(raw).parentElement;
    expect(cell?.className).toContain("col-[3/-1]");
    expect(cell?.className).not.toContain("col-[2/-1]");
  });

  it("says 1,000+ new when more arrived while paused than a tab keeps", () => {
    setState({ paused: true, pending: 4321 });
    render(<LiveLogsView />);
    expect(screen.getByText("1,000+ new")).toBeInTheDocument();
  });

  it("revokes the saved file's URL only after the download has started", async () => {
    const revoke = vi.fn();
    const created = vi.fn(() => "blob:live");
    const fakeUrl = { createObjectURL: created, revokeObjectURL: revoke };
    vi.stubGlobal("URL", Object.assign(Object.create(URL), fakeUrl));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    try {
      setState({ lines: { requests: [line()] } });
      render(<LiveLogsView />);
      fireEvent.click(screen.getByRole("button", { name: /save what/i }));
      expect(click).toHaveBeenCalledTimes(1);
      expect(revoke).not.toHaveBeenCalled();
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(revoke).toHaveBeenCalledWith("blob:live");
    } finally {
      click.mockRestore();
      vi.unstubAllGlobals();
    }
  });

  it("says when access was revoked", () => {
    setState({ status: "revoked" });
    render(<LiveLogsView />);
    expect(screen.getByText(/access was revoked/i)).toBeInTheDocument();
  });
});
