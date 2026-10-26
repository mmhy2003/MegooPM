import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { LiveLogsView } from "@/components/live-logs/live-logs-view";
import type { LiveLine, Tab } from "@/components/live-logs/lib";
import { CARD_HEIGHT, gridFor, ROW_HEIGHT } from "@/components/live-logs/log-rows";

const hook = vi.hoisted(() => ({ state: {} as Record<string, unknown>, calls: [] as unknown[] }));
vi.mock("@/components/live-logs/use-live-logs", () => ({
  useLiveLogs: (watch: unknown) => {
    hook.calls.push(watch);
    return hook.state;
  },
}));

// The layout under test is chosen deliberately, not by whatever width jsdom pretends to be.
const useIsMobile = vi.hoisted(() => vi.fn(() => false));
vi.mock("@/hooks/use-mobile", () => ({ useIsMobile }));

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
afterEach(() => {
  cleanup();
  useIsMobile.mockReturnValue(false);
});

type User = ReturnType<typeof userEvent.setup>;

/** Opens the host picker and waits for the host list to have loaded. */
async function openHostPicker(user: User) {
  await user.click(screen.getByRole("combobox", { name: "Add host" }));
  return screen.findByRole("listbox");
}

/** Picks each option, by name, from the host picker. */
async function pickHosts(user: User, ...names: string[]) {
  for (const name of names) {
    await openHostPicker(user);
    await user.click(await screen.findByRole("option", { name }));
  }
}

/**
 * Renders the page watching cloud.example.com (p1), the host `line()` is for:
 * only the picked hosts' lines are shown.
 */
async function renderWatchingP1() {
  const user = userEvent.setup();
  const result = render(<LiveLogsView />);
  await pickHosts(user, "cloud.example.com");
  return { user, ...result };
}

describe("LiveLogsView", () => {
  it("renders an attacker's user agent as literal text", async () => {
    const ua = "<img src=x onerror=alert(1)>";
    setState({ lines: { requests: [line({ f: { st: "200", m: "GET", u: "/", ip: "1.1.1.1", ua } })] } });
    const { container, user } = await renderWatchingP1();
    await user.click(screen.getByRole("button", { name: /GET/ }));
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
    await renderWatchingP1();
    expect(screen.getByText('{"ts":"1790000000.1","u":"/aaaa')).toBeInTheDocument();
    expect(screen.getByText("truncated")).toBeInTheDocument();
  });

  it("hides the node column when every line is from local", () => {
    setState({ lines: { requests: [line(), line()] } });
    render(<LiveLogsView />);
    expect(screen.queryByRole("columnheader", { name: "Node" })).toBeNull();
    expect(screen.queryByLabelText("Node")).toBeNull();
  });

  it("shows the node column when another node reported", async () => {
    setState({ lines: { requests: [line(), line({ node: "node-b" })] } });
    await renderWatchingP1();
    expect(screen.getByRole("columnheader", { name: "Node" })).toBeInTheDocument();
    expect(within(screen.getByRole("table")).getByText("node-b")).toBeInTheDocument();
  });

  it("tags a CrowdSec-denied request", async () => {
    setState({
      lines: { requests: [line({ f: { st: "403", m: "GET", u: "/x", ip: "1.1.1.1", cs: "denied" } })] },
    });
    await renderWatchingP1();
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
    await pickHosts(user, ALL_LOGS);
    await user.click(screen.getByRole("tab", { name: "Errors" }));
    const table = within(screen.getByRole("table"));
    expect(table.getByText("nginx")).toBeInTheDocument();
    expect(await table.findByText("cloud.example.com")).toBeInTheDocument();
  });

  const hostHeader = () => screen.queryByRole("columnheader", { name: "Host" });

  it("shows a Host column on Requests when hosts can mix, and not for one host", async () => {
    const user = userEvent.setup();
    setState({ lines: { requests: [line({ host: "p1" }), line({ host: "r2" })] } });
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
    expect(table.getByText("old.example.com")).toBeInTheDocument();

    const chips = within(screen.getByTestId("picked"));
    await user.click(chips.getByRole("button", { name: /old\.example/ }));
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

  it("shows only the picked host's lines once All logs is left for it", async () => {
    const user = userEvent.setup();
    setState({
      lines: {
        requests: [
          line({ host: "p1", f: { st: "200", m: "GET", u: "/from-p1" } }),
          line({ host: "r2", f: { st: "200", m: "GET", u: "/from-r2" } }),
        ],
      },
    });
    render(<LiveLogsView />);
    await pickHosts(user, ALL_LOGS);
    const table = within(screen.getByRole("table"));
    expect(table.getByText("/from-p1")).toBeInTheDocument();
    expect(table.getByText("/from-r2")).toBeInTheDocument();
    expect(hostHeader()).toBeInTheDocument();

    await pickHosts(user, "cloud.example.com");
    expect(table.getByText("/from-p1")).toBeInTheDocument();
    expect(table.queryByText("/from-r2")).toBeNull();
    expect(hostHeader()).toBeNull();
  });

  it("keeps CrowdSec decisions, which have no host, whatever hosts are picked", async () => {
    const user = userEvent.setup();
    setState({
      lines: {
        security: [
          line({ host: "", log: "decision", f: { type: "ban", scope: "Ip", value: "9.9.9.9" } }),
          line({ host: "r2", log: "security", f: null, raw: "[Crowdsec] denied on r2" }),
        ],
      },
    });
    render(<LiveLogsView />);
    await pickHosts(user, "cloud.example.com");
    await user.click(screen.getByRole("tab", { name: "Security" }));
    const table = within(screen.getByRole("table"));
    expect(table.getByText(/9\.9\.9\.9/)).toBeInTheDocument();
    expect(table.queryByText("[Crowdsec] denied on r2")).toBeNull();
  });

  it("names the nginx itself chip as the picker does", async () => {
    const user = userEvent.setup();
    render(<LiveLogsView />);
    await pickHosts(user, NGINX_ITSELF);
    const picked = within(screen.getByTestId("picked"));
    expect(picked.getByText(NGINX_ITSELF)).toBeInTheDocument();
    const stop = picked.getByRole("button", { name: `Stop watching ${NGINX_ITSELF}` });
    expect(stop).toBeInTheDocument();
  });

  it("says nginx's own messages are on Errors when only nginx itself is picked", async () => {
    const user = userEvent.setup();
    render(<LiveLogsView />);
    await pickHosts(user, NGINX_ITSELF);
    const hint = "nginx's own messages show on the Errors tab.";
    expect(screen.getByText(hint)).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Security" }));
    expect(screen.getByText(hint)).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Errors" }));
    expect(screen.queryByText(hint)).toBeNull();
    expect(screen.getByText("Waiting for traffic…")).toBeInTheDocument();
  });

  it("lets the Host column narrow, so the path keeps room on a small screen", () => {
    // jsdom's CSS parser drops clamp() from a style attribute, so the template
    // the rows are given is checked, not the DOM.
    for (const tab of ["requests", "streams"] as const) {
      const template = String(gridFor(tab, true, true).gridTemplateColumns).split(" ");
      expect(template[2]).toBe("clamp(6rem,18%,9rem)");
    }
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
    await pickHosts(user, "cloud.example.com");
    await user.click(screen.getByRole("combobox", { name: "Status filter" }));
    await user.click(await screen.findByRole("option", { name: "5xx" }));
    expect(screen.getByText("/broken")).toBeInTheDocument();
    expect(screen.queryByText("/fine")).toBeNull();
  });

  it("renders only the new row when a line arrives, and only the clicked one on select", async () => {
    const first = [line(), line(), line()];
    setState({ lines: { requests: first } });
    loads.done = 0;
    const { rerender } = await renderWatchingP1();
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

  it("puts only the rows in view in the page, and follows the scroll", async () => {
    const many = Array.from({ length: 1000 }, (_, i) =>
      line({ f: { st: "200", m: "GET", u: `/n${i}`, ip: "1.1.1.1" } }),
    );
    setState({ lines: { requests: many } });
    await renderWatchingP1();
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

  it("starts a truncated line after the node cell when the node column shows", async () => {
    const raw = '{"k":"p1","ts":"1790000000.1","u":"/aaaa';
    setState({
      lines: { requests: [line({ node: "node-b", f: null, raw, truncated: true })] },
    });
    await renderWatchingP1();
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
      await renderWatchingP1();
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

describe("LiveLogsView picked hosts", () => {
  it("shows no Watching line until a host is picked", () => {
    render(<LiveLogsView />);
    expect(screen.queryByTestId("picked")).toBeNull();
    expect(screen.queryByText("Watching")).toBeNull();
  });

  it("lists the picked hosts on their own line, after the controls", async () => {
    const user = userEvent.setup();
    render(<LiveLogsView />);
    await pickHosts(user, "cloud.example.com", "old.example.com");
    const picked = screen.getByTestId("picked");
    expect(within(picked).getByText("Watching")).toBeInTheDocument();
    expect(within(picked).getByText("cloud.example.com")).toBeInTheDocument();
    expect(within(picked).getByText("old.example.com")).toBeInTheDocument();
    // Not among the controls: picking a host must not push them along.
    const picker = screen.getByRole("combobox", { name: "Add host" });
    expect(picked.contains(picker)).toBe(false);
    expect(picker.compareDocumentPosition(picked) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const filter = screen.getByRole("searchbox", { name: "Filter lines" });
    expect(filter.compareDocumentPosition(picked) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("offers Clear all once two hosts are picked, and it stops watching them", async () => {
    const user = userEvent.setup();
    render(<LiveLogsView />);
    await pickHosts(user, "cloud.example.com");
    expect(screen.queryByRole("button", { name: "Clear all" })).toBeNull();
    await pickHosts(user, "old.example.com");
    await user.click(screen.getByRole("button", { name: "Clear all" }));
    expect(hook.calls.at(-1)).toEqual({ hosts: [], decisions: false });
    expect(screen.queryByTestId("picked")).toBeNull();
  });

  it("clears only the hosts this tab shows", async () => {
    const user = userEvent.setup();
    render(<LiveLogsView />);
    await user.click(screen.getByRole("tab", { name: "Stream hosts" }));
    await pickHosts(user, "Stream :2222");
    await user.click(screen.getByRole("tab", { name: "Requests" }));
    await pickHosts(user, "cloud.example.com", "old.example.com");
    await user.click(screen.getByRole("button", { name: "Clear all" }));
    expect(hook.calls.at(-1)).toEqual({ hosts: ["s3"], decisions: false });
  });
});

describe("LiveLogsView on a phone", () => {
  beforeEach(() => {
    useIsMobile.mockReturnValue(true);
  });

  async function renderPhoneWatchingP1() {
    const user = userEvent.setup();
    const result = render(<LiveLogsView />);
    await pickHosts(user, "cloud.example.com");
    return { user, ...result };
  }

  it("shows each request as a card with every field the table has, and no table", async () => {
    setState({
      lines: {
        requests: [
          line({
            f: { st: "502", m: "POST", u: "/upload", ip: "5.6.7.8", up: "10.0.0.12:80", rt: "0.038" },
          }),
        ],
      },
    });
    await renderPhoneWatchingP1();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("columnheader")).toBeNull();
    const card = within(screen.getByRole("region", { name: "Requests" })).getByRole("button");
    for (const text of ["502", "POST", "/upload", "38 ms", "10.0.0.12:80", "cloud.example.com"]) {
      expect(within(card).getByText(text, { exact: false })).toBeInTheDocument();
    }
    expect(within(card).getByText(/5\.6\.7\.8/)).toBeInTheDocument();
  });

  it("shows an error line's level, host and message", async () => {
    setState({
      lines: { errors: [line({ log: "error", sev: 3, f: null, raw: "upstream timed out (110)" })] },
    });
    const { user } = await renderPhoneWatchingP1();
    await user.click(screen.getByRole("tab", { name: "Errors" }));
    const card = within(screen.getByRole("region", { name: "Errors" })).getByRole("button");
    expect(within(card).getByText("error")).toBeInTheDocument();
    expect(within(card).getByText("cloud.example.com")).toBeInTheDocument();
    expect(within(card).getByText("upstream timed out (110)")).toBeInTheDocument();
  });

  it("names the stream tab short enough for four tabs to fit", () => {
    render(<LiveLogsView />);
    expect(screen.getByRole("tab", { name: "Streams" })).toBeInTheDocument();
  });

  it("opens every field of a tapped line in a panel, and copies it", async () => {
    setState({ lines: { requests: [line({ f: { st: "200", m: "GET", u: "/x", ua: "curl/8.1" } })] } });
    const { user } = await renderPhoneWatchingP1();
    await user.click(within(screen.getByRole("region", { name: "Requests" })).getByRole("button"));
    const panel = await screen.findByRole("dialog");
    expect(within(panel).getByText("curl/8.1")).toBeInTheDocument();

    await user.click(within(panel).getByRole("button", { name: "Copy line" }));
    // userEvent stands in for the clipboard; what was written reads back.
    expect(await navigator.clipboard.readText()).toContain("GET /x");

    await user.click(within(panel).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("moves the status and node filters into a Filters panel", async () => {
    setState({
      lines: {
        requests: [
          line({ f: { st: "200", m: "GET", u: "/fine" } }),
          line({ node: "node-b", f: { st: "502", m: "GET", u: "/broken" } }),
        ],
      },
    });
    const { user } = await renderPhoneWatchingP1();
    expect(screen.queryByRole("combobox", { name: "Status filter" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Node filter" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Filters" }));
    const panel = await screen.findByRole("dialog");
    expect(within(panel).getByRole("button", { name: "node-b" })).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "5xx" }));
    expect(within(panel).getByRole("button", { name: "5xx" })).toHaveAttribute("aria-pressed", "true");
    await user.click(within(panel).getByRole("button", { name: "Done" }));

    expect(screen.getByText("/broken")).toBeInTheDocument();
    expect(screen.queryByText("/fine")).toBeNull();
    expect(screen.getByRole("button", { name: "Filters, 1 on" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Filters, 1 on" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Reset" }));
    expect(screen.getByText("/fine")).toBeInTheDocument();
  });

  it("offers no Filters button where no filter applies", async () => {
    const user = userEvent.setup();
    render(<LiveLogsView />);
    await user.click(screen.getByRole("tab", { name: "Errors" }));
    expect(screen.queryByRole("button", { name: /^Filters/ })).toBeNull();
  });

  it("puts Save and Clear in a menu beside Pause", async () => {
    const clear = vi.fn();
    const setPaused = vi.fn();
    setState({ lines: { requests: [line()] }, clear, setPaused });
    const { user } = await renderPhoneWatchingP1();
    expect(screen.queryByRole("button", { name: /save what/i })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Pause" }));
    expect(setPaused).toHaveBeenCalledWith(true);

    await user.click(screen.getByRole("button", { name: "More actions" }));
    expect(await screen.findByRole("menuitem", { name: /save what/i })).toBeInTheDocument();
    await user.click(screen.getByRole("menuitem", { name: "Clear" }));
    expect(clear).toHaveBeenCalled();
  });

  it("puts only the cards in view in the page, and follows the scroll", async () => {
    const many = Array.from({ length: 1000 }, (_, i) =>
      line({ f: { st: "200", m: "GET", u: `/n${i}`, ip: "1.1.1.1" } }),
    );
    setState({ lines: { requests: many } });
    await renderPhoneWatchingP1();
    const list = screen.getByRole("region", { name: "Requests" });
    expect(within(list).getAllByRole("button").length).toBeLessThan(100);
    expect(within(list).getByText("/n999")).toBeInTheDocument();

    fireEvent.scroll(screen.getByTestId("rows"), { target: { scrollTop: CARD_HEIGHT * 990 } });
    expect(within(list).getByText("/n0")).toBeInTheDocument();
    expect(within(list).queryByText("/n999")).toBeNull();
  });
});
