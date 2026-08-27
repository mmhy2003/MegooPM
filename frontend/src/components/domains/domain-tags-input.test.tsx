import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import { DomainTagsInput } from "@/components/domains/domain-tags-input";

function Harness({
  initial = [],
  onPendingInvalidChange,
}: {
  initial?: string[];
  onPendingInvalidChange?: (invalid: boolean) => void;
}) {
  const [value, setValue] = useState<string[]>(initial);
  return (
    <>
      <label htmlFor="domains">Domain names</label>
      <DomainTagsInput
        id="domains"
        value={value}
        onChange={setValue}
        onPendingInvalidChange={onPendingInvalidChange}
      />
      <output data-testid="value">{value.join("|")}</output>
    </>
  );
}

const valueOf = () => screen.getByTestId("value").textContent;

describe("DomainTagsInput", () => {
  afterEach(() => cleanup());

  it("turns Enter, comma and space into tags, normalised and de-duplicated", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByLabelText("Domain names");
    await user.type(input, "Example.com{Enter}");
    await user.type(input, "b.com,");
    await user.type(input, "c.com ");
    await user.type(input, "example.com{Enter}");
    expect(valueOf()).toBe("example.com|b.com|c.com");
    expect(input).toHaveValue("");
    expect(screen.getByRole("button", { name: "Remove b.com" })).toBeInTheDocument();
  });

  it("splits a pasted list into tags", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByLabelText("Domain names");
    await user.click(input);
    await user.paste("a.com, b.com\n*.c.com");
    expect(valueOf()).toBe("a.com|b.com|*.c.com");
  });

  it("removes the last tag with Backspace on an empty input and a specific one via its button", async () => {
    const user = userEvent.setup();
    render(<Harness initial={["a.com", "b.com", "c.com"]} />);
    const input = screen.getByLabelText("Domain names");
    await user.click(input);
    await user.keyboard("{Backspace}");
    expect(valueOf()).toBe("a.com|b.com");
    await user.click(screen.getByRole("button", { name: "Remove a.com" }));
    expect(valueOf()).toBe("b.com");
  });

  it("keeps an invalid entry in the input, shows the error and notifies the parent", async () => {
    const user = userEvent.setup();
    const onInvalid = vi.fn();
    render(<Harness onPendingInvalidChange={onInvalid} />);
    const input = screen.getByLabelText("Domain names");
    await user.type(input, "bad_name{Enter}");
    expect(valueOf()).toBe("");
    expect(input).toHaveValue("bad_name");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("Not a valid domain name")).toBeInTheDocument();
    expect(onInvalid).toHaveBeenLastCalledWith(true);

    await user.clear(input);
    await user.type(input, "fixed.com{Enter}");
    expect(valueOf()).toBe("fixed.com");
    expect(onInvalid).toHaveBeenLastCalledWith(false);
  });

  it("commits valid pending text on blur", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByLabelText("Domain names");
    await user.type(input, "late.com");
    await user.tab();
    expect(valueOf()).toBe("late.com");
  });
});
