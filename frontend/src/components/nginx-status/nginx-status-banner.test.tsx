import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { nginx, type NginxApplyStatus } from "@/lib/api";
import { NginxStatusBanner } from "@/components/nginx-status/nginx-status-banner";

const useAuth = vi.hoisted(() => vi.fn(() => ({ user: { role: "admin" } })));
vi.mock("@/lib/auth/context", () => ({ useAuth }));

// Capture the event callback so a test can deliver an event by hand.
const events = vi.hoisted(() => ({ onEvent: null as null | ((type: string) => void) }));
vi.mock("@/lib/events", () => ({
  subscribeToEvents: (onEvent: (type: string) => void) => {
    events.onEvent = onEvent;
    return () => {
      events.onEvent = null;
    };
  },
}));

const NGINX_ERROR =
  'nginx: [emerg] host not found in upstream "myapp:8080" in /data/nginx/conf.d/megoopm-proxy-12.conf:31\n' +
  "nginx: configuration file /etc/nginx/nginx.conf test failed";

const FAILED: NginxApplyStatus = {
  ok: false,
  at: "2026-09-10T12:00:00Z",
  message: "Generated configuration failed `nginx -t`; rolled back, nginx untouched.",
  output: NGINX_ERROR,
};

beforeEach(() => {
  vi.spyOn(nginx, "status").mockResolvedValue(FAILED);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useAuth.mockReturnValue({ user: { role: "admin" } });
});

describe("NginxStatusBanner", () => {
  it("shows nginx's own words when the last apply failed", async () => {
    render(<NginxStatusBanner />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/not applying changes/i);
    expect(screen.getByText(/host not found in upstream/)).toBeInTheDocument();
  });

  it("says every change will keep rolling back until it is fixed", async () => {
    // The fact that makes this urgent: the host that broke the apply stays in
    // the database, so it is not one failed save but all of them from now on.
    render(<NginxStatusBanner />);

    expect(await screen.findByText(/every change will keep rolling back/i)).toBeInTheDocument();
  });

  it("names the object the error points at", async () => {
    render(<NginxStatusBanner />);

    const link = await screen.findByRole("link", { name: /proxy host #12/i });
    expect(link).toHaveAttribute("href", "/proxy-hosts");
  });

  it("shows nothing while the last apply succeeded", async () => {
    vi.mocked(nginx.status).mockResolvedValue({ ok: true, at: null, message: "ok", output: "" });
    render(<NginxStatusBanner />);

    await waitFor(() => expect(nginx.status).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows nothing when no apply has been recorded yet", async () => {
    // A fresh install must not be greeted by a failure nobody caused.
    vi.mocked(nginx.status).mockResolvedValue({ ok: null, at: null, message: null, output: null });
    render(<NginxStatusBanner />);

    await waitFor(() => expect(nginx.status).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not ask for the status on a member's behalf", async () => {
    // The endpoint is admin-only; a member would only collect 403s.
    useAuth.mockReturnValue({ user: { role: "member" } });
    render(<NginxStatusBanner />);

    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(nginx.status).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("appears the moment an apply fails, without a reload", async () => {
    vi.mocked(nginx.status).mockResolvedValue({ ok: true, at: null, message: "ok", output: "" });
    render(<NginxStatusBanner />);
    await waitFor(() => expect(nginx.status).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    vi.mocked(nginx.status).mockResolvedValue(FAILED);
    act(() => events.onEvent?.("config.failed"));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("clears itself when the next apply succeeds", async () => {
    render(<NginxStatusBanner />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();

    vi.mocked(nginx.status).mockResolvedValue({ ok: true, at: null, message: "ok", output: "" });
    act(() => events.onEvent?.("config.applied"));

    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("retries the apply on request", async () => {
    // For a fix made outside MegooPM — a hostname that now resolves, a
    // certificate that has arrived — where no save will trigger an apply.
    const user = userEvent.setup();
    const reload = vi
      .spyOn(nginx, "reload")
      .mockResolvedValue({ task_id: "t1", status: "PENDING" } as never);
    render(<NginxStatusBanner />);

    await user.click(await screen.findByRole("button", { name: /retry/i }));

    expect(reload).toHaveBeenCalledTimes(1);
  });
});
