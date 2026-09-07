import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

afterEach(cleanup);

function renderDialog(props: React.ComponentProps<typeof DialogContent> = {}) {
  render(
    <Dialog open>
      <DialogContent {...props}>
        <DialogTitle>Sized</DialogTitle>
      </DialogContent>
    </Dialog>,
  );
  return screen.getByRole("dialog");
}

describe("DialogContent sizes", () => {
  it("grows at the xl breakpoint, tier by tier", () => {
    // The point of the change: on a wide monitor a form or a table gets the
    // room it has, instead of a third of the screen and two thirds of overlay.
    expect(renderDialog({ size: "md" })).toHaveClass("max-w-lg", "xl:max-w-2xl");
    cleanup();
    expect(renderDialog({ size: "lg" })).toHaveClass("max-w-2xl", "xl:max-w-4xl");
    cleanup();
    expect(renderDialog({ size: "xl" })).toHaveClass("max-w-3xl", "xl:max-w-5xl");
  });

  it("keeps a confirmation small on every screen", () => {
    // Yes/No needs no room; wide would read as important.
    const dialog = renderDialog({ size: "sm" });

    expect(dialog).toHaveClass("max-w-md");
    expect(dialog.className).not.toMatch(/xl:max-w/);
  });

  it("defaults to md, so a dialog that names no size still behaves", () => {
    expect(renderDialog()).toHaveClass("max-w-lg", "xl:max-w-2xl");
  });

  it("never runs edge to edge on a phone", () => {
    // w-[calc(100%-2rem)]: the border must not touch both screen sides.
    expect(renderDialog({ size: "lg" })).toHaveClass("w-[calc(100%-2rem)]");
  });
});
