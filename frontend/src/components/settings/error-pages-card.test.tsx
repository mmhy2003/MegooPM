import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { instanceSettings } from "@/lib/api";
import { ErrorPagesCard } from "@/components/settings/error-pages-card";

const CODES = [400, 401, 403, 404, 500, 502, 503, 504];
const DEFAULTS = CODES.map((code) => ({
  code,
  mode: "default" as const,
  custom_page_id: null,
}));
const PAGES = [{ id: 4, name: "Maintenance" }] as never;

beforeEach(() => {
  vi.spyOn(instanceSettings, "listErrorPages").mockResolvedValue(DEFAULTS);
  vi.spyOn(toast, "success").mockImplementation(() => "" as never);
  vi.spyOn(toast, "error").mockImplementation(() => "" as never);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ErrorPagesCard", () => {
  it("lists every branded code with its name", async () => {
    render(<ErrorPagesCard pages={PAGES} />);
    for (const code of CODES) {
      expect(await screen.findByText(String(code))).toBeInTheDocument();
    }
    expect(screen.getByText("Bad gateway")).toBeInTheDocument();
  });

  it("shows a page picker only for the custom mode", async () => {
    const user = userEvent.setup();
    render(<ErrorPagesCard pages={PAGES} />);
    const row = (await screen.findByText("404")).closest("tr")!;
    expect(within(row).queryByRole("combobox", { name: /page for 404/i })).not.toBeInTheDocument();

    await user.click(within(row).getByRole("combobox", { name: /answer for 404/i }));
    await user.click(await screen.findByRole("option", { name: "Custom page" }));

    expect(within(row).getByRole("combobox", { name: /page for 404/i })).toBeInTheDocument();
  });

  it("keeps Save disabled until something changes", async () => {
    const user = userEvent.setup();
    render(<ErrorPagesCard pages={PAGES} />);
    const save = await screen.findByRole("button", { name: /save error pages/i });
    expect(save).toBeDisabled();

    const row = screen.getByText("404").closest("tr")!;
    await user.click(within(row).getByRole("combobox", { name: /answer for 404/i }));
    await user.click(await screen.findByRole("option", { name: "Custom page" }));
    await user.click(within(row).getByRole("combobox", { name: /page for 404/i }));
    await user.click(await screen.findByRole("option", { name: "Maintenance" }));

    expect(save).toBeEnabled();
  });

  it("sends all eight rows, with the page on the one that changed", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(instanceSettings, "updateErrorPages").mockResolvedValue(DEFAULTS);
    render(<ErrorPagesCard pages={PAGES} />);
    const row = (await screen.findByText("404")).closest("tr")!;

    await user.click(within(row).getByRole("combobox", { name: /answer for 404/i }));
    await user.click(await screen.findByRole("option", { name: "Custom page" }));
    await user.click(within(row).getByRole("combobox", { name: /page for 404/i }));
    await user.click(await screen.findByRole("option", { name: "Maintenance" }));
    await user.click(screen.getByRole("button", { name: /save error pages/i }));

    await waitFor(() => expect(update).toHaveBeenCalled());
    const sent = update.mock.calls[0][0];
    expect(sent).toHaveLength(8);
    expect(sent.find((r) => r.code === 404)).toEqual({
      code: 404,
      mode: "custom_page",
      custom_page_id: 4,
    });
    expect(sent.find((r) => r.code === 502)).toEqual({
      code: 502,
      mode: "default",
      custom_page_id: null,
    });
  });

  it("shows a load failure instead of an empty card", async () => {
    vi.mocked(instanceSettings.listErrorPages).mockRejectedValue(new Error("boom"));
    render(<ErrorPagesCard pages={PAGES} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });
});
