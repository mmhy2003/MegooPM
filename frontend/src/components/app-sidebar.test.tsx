import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AppSidebar } from "@/components/app-sidebar";
import { SidebarProvider } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
}));
vi.mock("@/lib/auth/context", () => ({
  useAuth: () => ({ user: { id: 1, role: "admin" } }),
}));

function renderSidebar(defaultOpen = true) {
  const { container } = render(
    <TooltipProvider>
      <SidebarProvider defaultOpen={defaultOpen}>
        <AppSidebar />
      </SidebarProvider>
    </TooltipProvider>,
  );
  const state = () => container.querySelector("[data-slot=sidebar]")?.getAttribute("data-state");
  return { state };
}

/** The open tooltip. base-ui's popup has no `role="tooltip"`; it carries the
 *  component's `data-slot` instead. */
function openTooltip(): Promise<HTMLElement> {
  return waitFor(() => {
    const tip = document.querySelector<HTMLElement>("[data-slot=tooltip-content][data-open]");
    if (!tip) throw new Error("the tooltip did not open");
    return tip;
  });
}

function onPlatform(platform: string) {
  vi.spyOn(window.navigator, "platform", "get").mockReturnValue(platform);
}

describe("AppSidebar collapse toggle", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("collapses from a button next to the logo", async () => {
    const user = userEvent.setup();
    const { state } = renderSidebar();
    expect(state()).toBe("expanded");

    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));

    expect(state()).toBe("collapsed");
  });

  it("expands from the toggle above the logo once collapsed", async () => {
    const user = userEvent.setup();
    const { state } = renderSidebar(false);
    expect(state()).toBe("collapsed");
    expect(screen.queryByRole("button", { name: "Collapse sidebar" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Expand sidebar" }));

    expect(state()).toBe("expanded");
  });

  it("keeps the logo, linking home, while collapsed", () => {
    renderSidebar(false);
    const logo = screen.getByRole("img", { name: /MegooPM logo/ });
    expect(logo.closest("a")).toHaveAttribute("href", "/");
    // Below the toggle, not swapped into it.
    const toggle = screen.getByRole("button", { name: "Expand sidebar" });
    expect(toggle.contains(logo)).toBe(false);
    expect(toggle.compareDocumentPosition(logo) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("still links the logo home while the sidebar is open", () => {
    renderSidebar();
    expect(screen.getByRole("link", { name: /MegooPM/ })).toHaveAttribute("href", "/");
  });

  it("says whether the sidebar is open to assistive technology", () => {
    renderSidebar();
    expect(screen.getByRole("button", { name: "Collapse sidebar" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    cleanup();
    renderSidebar(false);
    expect(screen.getByRole("button", { name: "Expand sidebar" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("shows Ctrl B in the tooltip and announces the shortcut on Windows and Linux", async () => {
    onPlatform("Win32");
    const user = userEvent.setup();
    renderSidebar();
    const toggle = screen.getByRole("button", { name: "Collapse sidebar" });
    expect(toggle).toHaveAttribute("aria-keyshortcuts", "Control+B");

    await user.hover(toggle);

    const tip = await openTooltip();
    expect(tip).toHaveTextContent("Collapse sidebar");
    expect(tip).toHaveTextContent("Ctrl");
    expect(tip).toHaveTextContent("B");
  });

  it("shows ⌘ B on a Mac", async () => {
    onPlatform("MacIntel");
    const user = userEvent.setup();
    renderSidebar(false);
    const toggle = screen.getByRole("button", { name: "Expand sidebar" });
    expect(toggle).toHaveAttribute("aria-keyshortcuts", "Meta+B");

    await user.hover(toggle);

    const tip = await openTooltip();
    expect(tip).toHaveTextContent("Expand sidebar");
    expect(tip).toHaveTextContent("⌘");
    expect(tip).not.toHaveTextContent("Ctrl");
  });
});
