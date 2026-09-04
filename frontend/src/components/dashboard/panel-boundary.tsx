"use client";

import { Component, type ReactNode } from "react";

/**
 * Keeps one panel's rendering failure from taking down the dashboard.
 *
 * The data layer already follows this rule — a source that fails empties its
 * own card and nothing else — but *rendering* had no equivalent, so a throw
 * inside one panel unmounted the whole page and left Next's error screen. A
 * dashboard is the page an operator opens when something is wrong; it losing
 * itself over a map is the worst possible moment to be fragile.
 *
 * A class because React offers no hook for this.
 */
export class PanelBoundary extends Component<
  { title: string; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    // Kept: the panel says only that it failed, and an operator needs
    // somewhere to find out why.
    console.error("dashboard panel failed:", error);
  }

  render() {
    if (this.state.failed) {
      return (
        <section className="bg-card text-card-foreground space-y-1 rounded-xl border p-4 shadow-xs">
          <h3 className="text-sm font-medium">{this.props.title}</h3>
          <p className="text-muted-foreground text-sm">
            This panel failed to render. The rest of the dashboard is
            unaffected; the browser console has the details.
          </p>
        </section>
      );
    }
    return this.props.children;
  }
}
