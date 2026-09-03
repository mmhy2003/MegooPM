import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { certificates, streams, upstreams, type Stream } from "@/lib/api";
import { StreamsView } from "@/components/streams/streams-view";

function makeStream(over: Partial<Stream> = {}): Stream {
  return {
    id: 1,
    incoming_port: 5432,
    forward_host: "10.0.0.5",
    forward_port: 5432,
    tcp_forwarding: true,
    udp_forwarding: false,
    certificate_id: null,
    upstream_id: null,
    enabled: true,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    ...over,
  } as Stream;
}

async function renderView(rows: Stream[]) {
  vi.spyOn(streams, "list").mockResolvedValue(rows);
  vi.spyOn(certificates, "list").mockResolvedValue([]);
  vi.spyOn(upstreams, "list").mockResolvedValue([]);
  render(<StreamsView />);
  await screen.findByRole("searchbox", { name: "Search streams" });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("StreamsView search", () => {
  it("matches the incoming port, which is a number", async () => {
    // The page stringifies the port; the shared matcher only ever sees strings.
    const user = userEvent.setup();
    await renderView([
      makeStream({ id: 1, incoming_port: 5432, forward_host: "10.0.0.5" }),
      makeStream({ id: 2, incoming_port: 6379, forward_host: "10.0.0.6" }),
    ]);

    await user.type(screen.getByRole("searchbox"), "6379");

    expect(screen.getByText("6379")).toBeInTheDocument();
    expect(screen.queryByText("5432")).not.toBeInTheDocument();
  });

  it("matches the forward host too", async () => {
    const user = userEvent.setup();
    await renderView([
      makeStream({ id: 1, incoming_port: 5432, forward_host: "10.0.0.5" }),
      makeStream({ id: 2, incoming_port: 6379, forward_host: "10.0.0.6" }),
    ]);

    await user.type(screen.getByRole("searchbox"), "10.0.0.5");

    expect(screen.getByText("5432")).toBeInTheDocument();
    expect(screen.queryByText("6379")).not.toBeInTheDocument();
  });

  it("distinguishes a filtered-empty table from an empty instance", async () => {
    const user = userEvent.setup();
    await renderView([]);
    expect(screen.getByText(/no streams yet/i)).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox"), "nonesuch");

    expect(screen.getByText(/no streams match/i)).toBeInTheDocument();
  });
});
