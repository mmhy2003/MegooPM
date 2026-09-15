import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { Count } from "@/components/ui/count";

afterEach(() => cleanup());

describe("Count", () => {
  it("shows a large number shortened, with the exact figure on hover", () => {
    render(<Count value={1266434} />);

    expect(screen.getByText("1.3M")).toBeInTheDocument();
    expect(screen.getByTitle("1,266,434")).toBeInTheDocument();
  });

  it("gives assistive technology the exact figure, not the abbreviation", () => {
    // "1.3M" read aloud is "one point three M"; the precision is what matters.
    render(<Count value={1266434} />);

    expect(screen.getByText("1.3M")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByText("1,266,434")).toBeInTheDocument();
  });

  it("renders a small number as plain text", () => {
    render(<Count value={42} />);

    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.queryByTitle("42")).not.toBeInTheDocument();
  });
});
