import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { toast } from "sonner";

import { Toaster } from "@/components/ui/sonner";

afterEach(cleanup);

describe("Toaster", () => {
  it("stacks toasts top-right, below the topbar", async () => {
    // sonner.tsx is shadcn-generated; a regeneration would quietly reset the
    // position to bottom-right. This pins the placement as an intent.
    render(<Toaster />);
    act(() => {
      toast.success("Saved");
    });

    const region = await waitFor(() => {
      const el = document.querySelector("[data-sonner-toaster]");
      if (!el) throw new Error("toaster not rendered yet");
      return el as HTMLElement;
    });

    expect(region).toHaveAttribute("data-y-position", "top");
    expect(region).toHaveAttribute("data-x-position", "right");
    // The h-14 topbar plus a gap: toasts must not slide in under the header.
    expect(region.style.getPropertyValue("--offset-top")).toBe("4.5rem");
  });
});
