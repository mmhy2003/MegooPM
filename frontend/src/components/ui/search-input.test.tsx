import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SearchInput } from "@/components/ui/search-input";

/** A controlled input needs an owner; without one, typing shows one character. */
function Harness({ initial = "" }: { initial?: string }) {
  const [value, setValue] = useState(initial);
  return <SearchInput value={value} onValueChange={setValue} label="Search proxy hosts" />;
}

afterEach(() => cleanup());

describe("SearchInput", () => {
  it("is reachable by its accessible name", () => {
    render(<Harness />);
    expect(screen.getByRole("searchbox", { name: "Search proxy hosts" })).toBeInTheDocument();
  });

  it("reports every keystroke", async () => {
    const user = userEvent.setup();
    const onValueChange = vi.fn();
    render(<SearchInput value="" onValueChange={onValueChange} label="Search proxy hosts" />);

    await user.type(screen.getByRole("searchbox"), "a");

    expect(onValueChange).toHaveBeenCalledWith("a");
  });

  it("shows no clear button while the box is empty", () => {
    render(<Harness />);
    expect(screen.queryByRole("button", { name: /clear/i })).not.toBeInTheDocument();
  });

  it("offers a clear button once there is something to clear", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.type(screen.getByRole("searchbox"), "api");

    expect(
      screen.getByRole("button", { name: "Clear search proxy hosts" }),
    ).toBeInTheDocument();
  });

  it("empties the box when cleared", async () => {
    const user = userEvent.setup();
    render(<Harness initial="api" />);

    await user.click(screen.getByRole("button", { name: "Clear search proxy hosts" }));

    expect(screen.getByRole("searchbox")).toHaveValue("");
  });
});
