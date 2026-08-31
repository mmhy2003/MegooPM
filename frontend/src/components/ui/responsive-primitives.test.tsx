/**
 * Sizing guarantees the shared primitives owe every consumer.
 *
 * These assert class names, which is normally a weak thing to test — but these
 * particular classes are load bearing and were missing in production. Eight
 * dialogs shipped unable to scroll because `DialogContent` did not provide a
 * max-height and each dialog was expected to remember one; the Security page's
 * fourth tab was unreachable on a phone because `TabsList` could not scroll.
 *
 * jsdom does no layout, so nothing here proves anything *fits*. It proves the
 * primitive still asks for the behaviour, which is the regression that actually
 * happened.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTab } from "@/components/ui/tabs";

afterEach(cleanup);

describe("DialogContent sizing", () => {
  function open(className?: string) {
    render(
      <Dialog open onOpenChange={() => {}}>
        <DialogContent className={className}>
          <DialogTitle>Title</DialogTitle>
        </DialogContent>
      </Dialog>,
    );
    return document.querySelector('[data-slot="dialog-content"]') as HTMLElement;
  }

  it("can scroll when it is taller than the viewport", () => {
    const el = open();
    expect(el.className).toContain("overflow-y-auto");
    expect(el.className).toMatch(/max-h-/);
  });

  it("leaves a gutter instead of running edge to edge on a phone", () => {
    expect(open().className).toContain("w-[calc(100%-2rem)]");
  });

  it("still lets a dialog widen itself", () => {
    // Sizing is the primitive's job; width is the dialog's.
    expect(open("max-w-2xl").className).toContain("max-w-2xl");
  });
});

describe("TabsList overflow", () => {
  function renderTabs() {
    render(
      <Tabs defaultValue="a">
        <TabsList>
          <TabsTab value="a">Dashboard</TabsTab>
          <TabsTab value="b">Active decisions</TabsTab>
          <TabsTab value="c">Recent alerts</TabsTab>
          <TabsTab value="d">Whitelists</TabsTab>
        </TabsList>
      </Tabs>,
    );
    return document.querySelector('[data-slot="tabs-list"]') as HTMLElement;
  }

  it("scrolls rather than putting later tabs out of reach", () => {
    const list = renderTabs();
    expect(list.className).toContain("overflow-x-auto");
    expect(list.className).toContain("max-w-full");
  });

  it("aligns to the start so the first tab stays reachable when scrolling", () => {
    // A centred flex container whose content overflows spills out of both ends,
    // and the start cannot be scrolled back to.
    const list = renderTabs();
    expect(list.className).toContain("justify-start");
    expect(list.className).not.toContain("justify-center");
  });

  it("keeps every tab rendered and labelled", () => {
    renderTabs();
    expect(screen.getAllByRole("tab")).toHaveLength(4);
    expect(screen.getByRole("tab", { name: "Whitelists" })).toBeInTheDocument();
  });

  it("does not squeeze tabs below their label width", () => {
    renderTabs();
    const tab = screen.getByRole("tab", { name: "Active decisions" });
    expect(tab.className).toContain("shrink-0");
    expect(tab.className).toContain("whitespace-nowrap");
  });
});
