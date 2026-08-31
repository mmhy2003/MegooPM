import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { WhitelistStatusBanner } from "@/components/security/whitelist-status-banner";

afterEach(cleanup);

describe("WhitelistStatusBanner", () => {
  it("renders nothing when the last apply succeeded", () => {
    const { container } = render(
      <WhitelistStatusBanner
        status={{ ok: true, error: null, applied_at: null, reload_configured: true }}
        onRetry={() => {}}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the failure text so a failed reload is not invisible", () => {
    // The save returns 200 before the apply runs; without this the table would
    // imply a whitelist is in force when CrowdSec has never seen it.
    render(
      <WhitelistStatusBanner
        status={{
          ok: false,
          error: "CrowdSec did not come back within 60s.",
          applied_at: null,
          reload_configured: true,
        }}
        onRetry={() => {}}
      />,
    );
    expect(screen.getByText(/did not come back within 60s/)).toBeInTheDocument();
  });

  it("warns when reloads are not configured at all", () => {
    render(
      <WhitelistStatusBanner
        status={{ ok: true, error: null, applied_at: null, reload_configured: false }}
        onRetry={() => {}}
      />,
    );
    expect(screen.getByText(/CROWDSEC_CONTROL_NODE_ID/)).toBeInTheDocument();
  });

  it("offers no retry when there is no node to retry on", () => {
    render(
      <WhitelistStatusBanner
        status={{ ok: true, error: null, applied_at: null, reload_configured: false }}
        onRetry={() => {}}
      />,
    );
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
  });

  it("retries on demand", async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(
      <WhitelistStatusBanner
        status={{ ok: false, error: "boom", applied_at: null, reload_configured: true }}
        onRetry={onRetry}
      />,
    );
    await user.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
