import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { WhitelistDialog } from "@/components/security/whitelist-dialog";

const previewWhitelist = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    crowdsec: { previewWhitelist: (...args: unknown[]) => previewWhitelist(...args) },
  };
});

afterEach(() => {
  cleanup();
  previewWhitelist.mockReset();
});

function open(onSubmit = vi.fn().mockResolvedValue(undefined)) {
  render(
    <WhitelistDialog
      open
      onOpenChange={() => {}}
      whitelist={null}
      onSubmit={onSubmit}
    />,
  );
  return onSubmit;
}

describe("WhitelistDialog", () => {
  it("refuses a malformed IP without calling the API", async () => {
    const user = userEvent.setup();
    const onSubmit = open();

    await user.type(screen.getByLabelText("Name"), "Internal");
    await user.type(screen.getByLabelText("Reason"), "internal backends");
    await user.type(screen.getByLabelText("IP addresses"), "10.10.0.999");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(
      await screen.findByText("'10.10.0.999' is not a valid IP address."),
    ).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("refuses a malformed CIDR", async () => {
    const user = userEvent.setup();
    const onSubmit = open();

    await user.type(screen.getByLabelText("Name"), "Internal");
    await user.type(screen.getByLabelText("Reason"), "internal backends");
    await user.type(screen.getByLabelText("CIDR ranges"), "10.10.0.0/99");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(
      await screen.findByText("'10.10.0.0/99' is not a valid CIDR range."),
    ).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("refuses a whitelist that would match nothing", async () => {
    const user = userEvent.setup();
    const onSubmit = open();

    await user.type(screen.getByLabelText("Name"), "Internal");
    await user.type(screen.getByLabelText("Reason"), "internal backends");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(
      await screen.findByText(/at least one IP address or CIDR range/),
    ).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("accepts one address per line", async () => {
    const user = userEvent.setup();
    const onSubmit = open();

    await user.type(screen.getByLabelText("Name"), "Internal");
    await user.type(screen.getByLabelText("Reason"), "internal backends");
    await user.type(screen.getByLabelText("IP addresses"), "10.10.0.14{enter}10.10.0.15");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({ ips: ["10.10.0.14", "10.10.0.15"], cidrs: [] }),
      ),
    );
  });

  it("shows the YAML the server would write", async () => {
    // Rendered by the backend, not re-implemented here: a second renderer
    // would drift from the file that actually reaches CrowdSec.
    previewWhitelist.mockResolvedValue({
      yaml: 'name: "megoopm/wl-internal"\n',
    });
    const user = userEvent.setup();
    open();

    await user.type(screen.getByLabelText("Name"), "Internal");
    await user.type(screen.getByLabelText("Reason"), "r");
    await user.type(screen.getByLabelText("IP addresses"), "10.10.0.14");

    expect(await screen.findByText(/megoopm\/wl-internal/)).toBeInTheDocument();
  });

  it("asks for no preview until there is something to render", async () => {
    const user = userEvent.setup();
    open();
    await user.type(screen.getByLabelText("Name"), "Internal");
    await waitFor(() => expect(previewWhitelist).not.toHaveBeenCalled());
  });
});
