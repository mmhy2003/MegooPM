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

  it("can never scroll sideways, whatever it holds", () => {
    // The dialog is a grid with overflow-y auto. A grid child defaults to
    // min-width:auto, so anything whose minimum content is wider than a
    // narrow screen (a Fold's 344px cover display) pushes the grid wider —
    // and an auto overflow-y forces overflow-x to auto, so that push becomes
    // a horizontal scrollbar on the whole popup. Letting children shrink and
    // clipping x is what keeps it to a single vertical scroll.
    const dialog = renderDialog();

    expect(dialog).toHaveClass("overflow-x-hidden", "[&>*]:min-w-0");
  });

  it("wraps a long title rather than widening the dialog", () => {
    render(
      <Dialog open>
        <DialogContent>
          <DialogTitle>
            averyveryverylongsubdomainnamethatwillnotfitonanarrowscreen.example.com
          </DialogTitle>
        </DialogContent>
      </Dialog>,
    );

    expect(screen.getByText(/averyvery/)).toHaveClass("break-words");
  });
});
