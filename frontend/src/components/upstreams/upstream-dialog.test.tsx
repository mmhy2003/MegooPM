import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { upstreams, type Upstream } from "@/lib/api";
import { UpstreamDialog } from "@/components/upstreams/upstream-dialog";

function makePool(over: Partial<Upstream> = {}): Upstream {
  return {
    id: 1,
    name: "app-pool",
    description: "",
    lb_method: "round_robin",
    context: "http",
    enabled: true,
    backends: [{ id: 1, upstream_id: 1, host: "10.0.0.1", port: 8080 } as never],
    created_at: "2026-08-27T00:00:00Z",
    updated_at: "2026-08-27T00:00:00Z",
    ...over,
  };
}

function renderDialog(pool: Upstream | null = makePool()) {
  return render(
    <UpstreamDialog open onOpenChange={() => {}} upstream={pool} onSaved={() => {}} />,
  );
}

describe("UpstreamDialog context", () => {
  beforeEach(() => {
    vi.spyOn(upstreams, "update").mockResolvedValue(makePool());
    vi.spyOn(upstreams, "create").mockResolvedValue(makePool());
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("exposes the pool's context", () => {
    renderDialog();
    expect(screen.getByLabelText("Context")).toBeInTheDocument();
  });

  it("offers ip_hash for an HTTP-only pool", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByLabelText("Load-balancing method"));
    expect(await screen.findByRole("option", { name: /IP hash/i })).toBeInTheDocument();
  });

  it("hides ip_hash once the pool can back a stream", async () => {
    const user = userEvent.setup();
    renderDialog(makePool({ context: "stream" }));
    await user.click(screen.getByLabelText("Load-balancing method"));
    // ip_hash is not a stream directive; offering it would only earn a 422.
    expect(screen.queryByRole("option", { name: /IP hash/i })).not.toBeInTheDocument();
  });

  it("resets ip_hash when the context stops being HTTP-only", async () => {
    const user = userEvent.setup();
    renderDialog(makePool({ lb_method: "ip_hash" }));

    await user.click(screen.getByLabelText("Context"));
    await user.click(await screen.findByRole("option", { name: /Both/i }));

    // Leaving ip_hash selected would submit a combination the API rejects.
    expect(screen.getByLabelText("Load-balancing method")).toHaveTextContent(/round.robin/i);
  });

  it("submits the chosen context", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByLabelText("Context"));
    await user.click(await screen.findByRole("option", { name: /Streams only/i }));
    await user.click(screen.getByRole("button", { name: /Save/i }));

    await waitFor(() => expect(upstreams.update).toHaveBeenCalled());
    expect(vi.mocked(upstreams.update).mock.calls[0][1]).toMatchObject({ context: "stream" });
  });
});
