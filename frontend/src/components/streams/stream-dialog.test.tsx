import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { streams, type Stream } from "@/lib/api";
import { StreamDialog } from "@/components/streams/stream-dialog";

function makeStream(over: Partial<Stream> = {}): Stream {
  return {
    id: 1,
    incoming_port: 5432,
    forward_host: "db.internal",
    forward_port: 5432,
    tcp_forwarding: true,
    udp_forwarding: false,
    certificate_id: null,
    enabled: true,
    created_at: "2026-08-30T00:00:00Z",
    updated_at: "2026-08-30T00:00:00Z",
    ...over,
  };
}

function renderDialog(stream: Stream | null = makeStream()) {
  return render(
    <StreamDialog
      open
      onOpenChange={() => {}}
      stream={stream}
      certificates={[]}
      onSaved={() => {}}
    />,
  );
}

describe("StreamDialog", () => {
  beforeEach(() => {
    vi.spyOn(streams, "update").mockResolvedValue(makeStream());
    vi.spyOn(streams, "create").mockResolvedValue(makeStream());
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("splits the form into Details and SSL tabs", () => {
    renderDialog();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Details", "SSL"]);
  });

  it("keeps the forwarding fields and protocol switches on Details", () => {
    renderDialog();
    expect(screen.getByLabelText("Incoming port")).toBeInTheDocument();
    expect(screen.getByLabelText("Forward port")).toBeInTheDocument();
    expect(screen.getByLabelText("Forward host")).toBeInTheDocument();
    // TCP/UDP are forwarding concerns and apply with no certificate at all.
    expect(screen.getByLabelText("TCP")).toBeInTheDocument();
    expect(screen.getByLabelText("UDP")).toBeInTheDocument();
    expect(screen.getByLabelText("Enabled")).toBeInTheDocument();
    expect(screen.queryByLabelText("SSL certificate")).not.toBeInTheDocument();
  });

  it("puts the certificate on the SSL tab", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("tab", { name: "SSL" }));
    expect(await screen.findByLabelText("SSL certificate")).toBeInTheDocument();
    // Raw TCP/UDP has no Force SSL / HSTS / HTTP2 equivalent.
    expect(screen.queryByLabelText("Force SSL")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Incoming port")).not.toBeInTheDocument();
  });

  it("jumps back to Details when validation fails on a hidden field", async () => {
    const user = userEvent.setup();
    renderDialog(makeStream({ forward_host: "" }));
    await user.click(screen.getByRole("tab", { name: "SSL" }));
    await screen.findByLabelText("SSL certificate");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a forward host.");
    expect(screen.getByRole("tab", { name: "Details" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(streams.update).not.toHaveBeenCalled();
  });

  it("reports the no-protocol error on Details too", async () => {
    const user = userEvent.setup();
    renderDialog(makeStream({ tcp_forwarding: false, udp_forwarding: false }));
    await user.click(screen.getByRole("tab", { name: "SSL" }));
    await screen.findByLabelText("SSL certificate");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enable at least one protocol (TCP or UDP).",
    );
    expect(screen.getByRole("tab", { name: "Details" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("submits the same payload as before the fields were split", async () => {
    const user = userEvent.setup();
    renderDialog(
      makeStream({
        incoming_port: 6379,
        forward_host: "cache.internal",
        forward_port: 6380,
        tcp_forwarding: true,
        udp_forwarding: true,
        certificate_id: 4,
      }),
    );
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(streams.update).toHaveBeenCalledTimes(1));
    expect(vi.mocked(streams.update).mock.calls[0][1]).toMatchObject({
      incoming_port: 6379,
      forward_host: "cache.internal",
      forward_port: 6380,
      tcp_forwarding: true,
      udp_forwarding: true,
      certificate_id: 4,
      enabled: true,
    });
  });
});
