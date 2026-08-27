import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { dnsCredentials } from "@/lib/api";
import { CertificateDialog } from "@/components/certificates/certificate-dialog";

describe("CertificateDialog", () => {
  beforeEach(() => {
    vi.spyOn(dnsCredentials, "list").mockResolvedValue([]);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("loads saved DNS credentials when opened and hides the picker for HTTP-01", async () => {
    render(<CertificateDialog open onOpenChange={() => {}} onSaved={() => {}} />);
    await waitFor(() => expect(dnsCredentials.list).toHaveBeenCalledTimes(1));
    expect(screen.queryByLabelText("DNS credentials")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Challenge")).toBeInTheDocument();
  });
});
