import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SearchableSelect } from "@/components/ui/searchable-select";
import { Label } from "@/components/ui/label";

const POOLS: Record<string, string> = {
  "1": "web-pool",
  "2": "api-pool",
  "3": "db-pool",
  "4": "cache-pool",
  "5": "mail-relay",
};

function renderPicker(over: Partial<React.ComponentProps<typeof SearchableSelect>> = {}) {
  const onValueChange = vi.fn();
  render(
    <div>
      <Label htmlFor="pool">Upstream pool</Label>
      <SearchableSelect
        id="pool"
        value="1"
        items={POOLS}
        onValueChange={onValueChange}
        placeholder="Choose a pool"
        {...over}
      />
    </div>,
  );
  return onValueChange;
}

afterEach(cleanup);

describe("SearchableSelect", () => {
  it("shows the chosen item's label, not its value", () => {
    // The same trap the plain Select had: the raw id tells an operator nothing.
    renderPicker();

    expect(screen.getByRole("combobox", { name: "Upstream pool" })).toHaveTextContent("web-pool");
  });

  it("opens to every option, then narrows as you type", async () => {
    const user = userEvent.setup();
    renderPicker();

    await user.click(screen.getByRole("combobox", { name: "Upstream pool" }));
    expect(await screen.findAllByRole("option")).toHaveLength(5);

    await user.type(await screen.findByLabelText("Search options"), "pool");
    expect(screen.getAllByRole("option")).toHaveLength(4);

    await user.type(await screen.findByLabelText("Search options"), " web");
    // queryAll: getAll throws on zero matches, which is the point of this step.
    expect(screen.queryAllByRole("option")).toHaveLength(0);
  });

  it("matches anywhere in the label, ignoring case", async () => {
    // "API" for api-pool, "relay" for mail-relay: an operator remembers a
    // fragment, rarely the prefix.
    const user = userEvent.setup();
    renderPicker();

    await user.click(screen.getByRole("combobox", { name: "Upstream pool" }));
    await user.type(await screen.findByLabelText("Search options"), "RELAY");

    expect(screen.getAllByRole("option")).toHaveLength(1);
    expect(screen.getByRole("option", { name: "mail-relay" })).toBeInTheDocument();
  });

  it("says so when nothing matches", async () => {
    const user = userEvent.setup();
    renderPicker();

    await user.click(screen.getByRole("combobox", { name: "Upstream pool" }));
    await user.type(await screen.findByLabelText("Search options"), "zzz");

    expect(screen.getByText("No matches.")).toBeInTheDocument();
  });

  it("selects with the pointer and reports the value, not the label", async () => {
    const user = userEvent.setup();
    const onValueChange = renderPicker();

    await user.click(screen.getByRole("combobox", { name: "Upstream pool" }));
    await user.click(await screen.findByRole("option", { name: "db-pool" }));

    expect(onValueChange).toHaveBeenCalledWith("3");
  });

  it("selects with the keyboard from a narrowed list", async () => {
    const user = userEvent.setup();
    const onValueChange = renderPicker();

    await user.click(screen.getByRole("combobox", { name: "Upstream pool" }));
    await user.type(await screen.findByLabelText("Search options"), "cache");
    await user.keyboard("{ArrowDown}{Enter}");

    expect(onValueChange).toHaveBeenCalledWith("4");
  });

  it("forgets the filter when it closes, so the next open shows everything", async () => {
    const user = userEvent.setup();
    renderPicker();

    await user.click(screen.getByRole("combobox", { name: "Upstream pool" }));
    await user.type(await screen.findByLabelText("Search options"), "db");
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("combobox", { name: "Upstream pool" }));
    expect(await screen.findAllByRole("option")).toHaveLength(5);
  });

  it("shows the placeholder while nothing is chosen", () => {
    renderPicker({ value: "" });

    expect(screen.getByRole("combobox", { name: "Upstream pool" })).toHaveTextContent(
      "Choose a pool",
    );
  });

  it("is inert when disabled", async () => {
    const user = userEvent.setup();
    renderPicker({ disabled: true });

    const trigger = screen.getByRole("combobox", { name: "Upstream pool" });
    expect(trigger).toBeDisabled();
    await user.click(trigger);
    expect(screen.queryByRole("option")).not.toBeInTheDocument();
  });

  it("takes an aria-label for pickers that have no visible label", () => {
    // One per table row, where a <Label> per row would be noise.
    render(
      <SearchableSelect
        aria-label="Page for 404"
        value=""
        items={POOLS}
        onValueChange={() => {}}
      />,
    );

    expect(screen.getByRole("combobox", { name: "Page for 404" })).toBeInTheDocument();
  });

  it("shows a disabled option but will not select it", async () => {
    // A certificate that is not active must stay visible — hiding it reads as
    // "it does not exist" — while refusing to be chosen.
    const user = userEvent.setup();
    const onValueChange = renderPicker({ disabledValues: ["3"] });

    await user.click(screen.getByRole("combobox", { name: "Upstream pool" }));
    const option = await screen.findByRole("option", { name: "db-pool" });
    expect(option).toHaveAttribute("aria-disabled", "true");
    await user.click(option);

    expect(onValueChange).not.toHaveBeenCalled();
  });
});
