import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

vi.mock("qrcode.react", () => ({
  QRCodeSVG: ({ value }: { value: string }) => <div data-testid="qr">{value}</div>,
}));

import { users } from "@/lib/api";
import { ApiError } from "@/lib/api/errors";
import { TotpCard } from "@/components/profile/totp-card";

const SETUP = {
  secret: "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
  otpauth_uri: "otpauth://totp/MegooPM:me?secret=GEZD",
};
const CODES = { codes: Array.from({ length: 10 }, (_, i) => `ABCDE-FGHJ${i}`) };

beforeEach(() => {
  vi.spyOn(toast, "success").mockImplementation(() => "" as never);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("TotpCard when off", () => {
  it("offers to enable", () => {
    render(<TotpCard enabled={false} onChanged={() => {}} />);
    expect(screen.getByRole("button", { name: /enable/i })).toBeInTheDocument();
  });

  it("shows the QR and the secret after setup starts", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "totpSetup").mockResolvedValue(SETUP);
    render(<TotpCard enabled={false} onChanged={() => {}} />);

    await user.click(screen.getByRole("button", { name: /enable/i }));

    expect(await screen.findByTestId("qr")).toHaveTextContent(SETUP.otpauth_uri);
    expect(screen.getByText(/GEZD GNBV/)).toBeInTheDocument();
  });

  it("shows the recovery codes once after a correct code", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "totpSetup").mockResolvedValue(SETUP);
    vi.spyOn(users, "totpEnable").mockResolvedValue(CODES);
    const onChanged = vi.fn();
    render(<TotpCard enabled={false} onChanged={onChanged} />);
    await user.click(screen.getByRole("button", { name: /enable/i }));
    await screen.findByTestId("qr");

    await user.type(screen.getByLabelText("Code from your app"), "123456");
    await user.click(screen.getByRole("button", { name: /confirm/i }));

    expect(await screen.findByText("ABCDE-FGHJ0")).toBeInTheDocument();
    expect(screen.getByText(/only time/i)).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
  });

  it("keeps the setup screen up on a wrong code", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "totpSetup").mockResolvedValue(SETUP);
    vi.spyOn(users, "totpEnable").mockRejectedValue(
      new ApiError(400, "Bad request", { detail: "That code is not valid." }),
    );
    render(<TotpCard enabled={false} onChanged={() => {}} />);
    await user.click(screen.getByRole("button", { name: /enable/i }));
    await screen.findByTestId("qr");

    await user.type(screen.getByLabelText("Code from your app"), "000000");
    await user.click(screen.getByRole("button", { name: /confirm/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/not valid/i);
    expect(screen.getByTestId("qr")).toBeInTheDocument();
  });

  it("requires acknowledgement before leaving the codes screen", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "totpSetup").mockResolvedValue(SETUP);
    vi.spyOn(users, "totpEnable").mockResolvedValue(CODES);
    render(<TotpCard enabled={false} onChanged={() => {}} />);
    await user.click(screen.getByRole("button", { name: /enable/i }));
    await user.type(await screen.findByLabelText("Code from your app"), "123456");
    await user.click(screen.getByRole("button", { name: /confirm/i }));
    await screen.findByText("ABCDE-FGHJ0");

    expect(screen.getByRole("button", { name: /done/i })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /saved these/i }));
    expect(screen.getByRole("button", { name: /done/i })).toBeEnabled();
  });
});

describe("TotpCard when on", () => {
  it("asks for a code before disabling", async () => {
    const user = userEvent.setup();
    render(<TotpCard enabled onChanged={() => {}} />);

    await user.click(screen.getByRole("button", { name: /disable/i }));

    expect(screen.getByLabelText("Code")).toBeInTheDocument();
  });

  it("disables with a valid code", async () => {
    const user = userEvent.setup();
    const disable = vi.spyOn(users, "totpDisable").mockResolvedValue(undefined);
    const onChanged = vi.fn();
    render(<TotpCard enabled onChanged={onChanged} />);
    await user.click(screen.getByRole("button", { name: /disable/i }));

    await user.type(screen.getByLabelText("Code"), "ABCDE-FGHJ0");
    await user.click(screen.getByRole("button", { name: /turn off/i }));

    await waitFor(() => expect(disable).toHaveBeenCalledWith("ABCDE-FGHJ0"));
    expect(onChanged).toHaveBeenCalled();
  });

  it("regenerates and shows the new codes once", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "totpRegenerate").mockResolvedValue(CODES);
    render(<TotpCard enabled onChanged={() => {}} />);
    await user.click(screen.getByRole("button", { name: /regenerate/i }));

    await user.type(screen.getByLabelText("Code"), "123456");
    await user.click(screen.getByRole("button", { name: /generate new codes/i }));

    expect(await screen.findByText("ABCDE-FGHJ0")).toBeInTheDocument();
  });
});
