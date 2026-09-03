import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { upstreams, type Upstream } from "@/lib/api";
import { UpstreamsView } from "@/components/upstreams/upstreams-view";

function makePool(over: Partial<Upstream> = {}): Upstream {
  return {
    id: 1,
    name: "app-pool",
    description: "",
    lb_method: "round_robin",
    enabled: true,
    backends: [],
    created_at: "2026-08-27T00:00:00Z",
    updated_at: "2026-08-27T00:00:00Z",
    ...over,
  } as Upstream;
}

describe("UpstreamsView", () => {
  beforeEach(() => {
    vi.spyOn(toast, "error").mockImplementation(() => "" as never);
    vi.spyOn(upstreams, "list").mockResolvedValue([makePool()]);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("lists pools on their own page, with no tab to open first", async () => {
    render(<UpstreamsView />);
    expect(await screen.findByText("app-pool")).toBeInTheDocument();
    // Pools used to live behind an "Upstream pools" tab on the proxy hosts page.
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  it("toggles a pool", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(upstreams, "update").mockResolvedValue(makePool({ enabled: false }));
    render(<UpstreamsView />);

    await user.click(await screen.findByLabelText("Enable app-pool"));

    await waitFor(() => expect(update).toHaveBeenCalledWith(1, { enabled: false }));
  });

  it("reverts a toggle that fails", async () => {
    const user = userEvent.setup();
    vi.spyOn(upstreams, "update").mockRejectedValue(new Error("nope"));
    render(<UpstreamsView />);

    const toggle = await screen.findByLabelText("Enable app-pool");
    await user.click(toggle);

    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
    expect(toast.error).toHaveBeenCalled();
  });
});

describe("UpstreamsView context column", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows each pool's context", async () => {
    vi.spyOn(upstreams, "list").mockResolvedValue([
      makePool({ id: 1, name: "web", context: "http" }),
      makePool({ id: 2, name: "db", context: "stream" }),
      makePool({ id: 3, name: "shared", context: "both" }),
    ]);
    render(<UpstreamsView />);

    expect(await screen.findByText("HTTP")).toBeInTheDocument();
    expect(screen.getByText("Streams")).toBeInTheDocument();
    expect(screen.getByText("Both")).toBeInTheDocument();
  });

  it("heads the column", async () => {
    vi.spyOn(upstreams, "list").mockResolvedValue([makePool()]);
    render(<UpstreamsView />);
    expect(
      await screen.findByRole("columnheader", { name: "Context" }),
    ).toBeInTheDocument();
  });
});

describe("UpstreamsView search", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("matches a pool by name and by backend host", async () => {
    const user = userEvent.setup();
    vi.spyOn(upstreams, "list").mockResolvedValue([
      makePool({
        id: 1,
        name: "api-pool",
        backends: [
          {
            id: 1,
            upstream_id: 1,
            host: "10.0.0.5",
            port: 8080,
            weight: 1,
            max_fails: 1,
            fail_timeout_seconds: 10,
            backup: false,
            down: false,
            enabled: true,
            created_at: "2026-09-01T00:00:00Z",
            updated_at: "2026-09-01T00:00:00Z",
          },
        ],
      }),
      makePool({ id: 2, name: "blog-pool", backends: [] }),
    ] as Upstream[]);
    render(<UpstreamsView />);
    await screen.findByRole("searchbox", { name: "Search upstream pools" });

    await user.type(screen.getByRole("searchbox"), "10.0.0.5");

    expect(screen.getByText("api-pool")).toBeInTheDocument();
    expect(screen.queryByText("blog-pool")).not.toBeInTheDocument();
  });

  it("distinguishes a filtered-empty table from an empty instance", async () => {
    const user = userEvent.setup();
    vi.spyOn(upstreams, "list").mockResolvedValue([]);
    render(<UpstreamsView />);
    await screen.findByRole("searchbox", { name: "Search upstream pools" });
    expect(screen.getByText(/no upstream pools yet/i)).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox"), "nonesuch");

    expect(screen.getByText(/no upstream pools match/i)).toBeInTheDocument();
  });
});
