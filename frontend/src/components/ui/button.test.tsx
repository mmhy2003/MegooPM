import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { Button } from "@/components/ui/button";

afterEach(cleanup);

describe("Button ghost-destructive", () => {
  it("reads as destructive at rest, not only on hover", () => {
    // A delete icon in the muted colour is indistinguishable from Edit beside
    // it until the tooltip is read. Red at rest is the affordance.
    render(
      <Button variant="ghost-destructive" size="icon-sm" aria-label="Delete thing">
        x
      </Button>,
    );

    const button = screen.getByRole("button", { name: "Delete thing" });
    expect(button).toHaveClass("text-destructive");
    expect(button).toHaveClass("hover:bg-destructive/10");
  });
});
