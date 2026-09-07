import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { RowCard, RowCards } from "@/components/ui/row-cards";

afterEach(cleanup);

describe("RowCards", () => {
  it("shows skeletons while loading, and no list", () => {
    render(
      <RowCards loading isEmpty={false} empty="Nothing">
        <RowCard title="should not render yet" />
      </RowCards>,
    );

    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    expect(screen.queryByText("should not render yet")).not.toBeInTheDocument();
  });

  it("shows the empty message instead of an empty list", () => {
    // An empty bordered box reads as "still loading"; a sentence reads as done.
    render(
      <RowCards loading={false} isEmpty empty="No proxy hosts yet.">
        {null}
      </RowCards>,
    );

    expect(screen.getByText("No proxy hosts yet.")).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("lays a row out as title, meta, facts and actions", () => {
    render(
      <RowCards loading={false} isEmpty={false} empty="—">
        <RowCard
          title="app.example.com"
          meta={<span>on</span>}
          facts={[
            { label: "Upstream", value: "web-pool" },
            { label: "Scheme", value: "https" },
          ]}
          actions={<button type="button">Edit</button>}
        />
      </RowCards>,
    );

    expect(screen.getByRole("listitem")).toBeInTheDocument();
    expect(screen.getByText("app.example.com")).toBeInTheDocument();
    expect(screen.getByText("on")).toBeInTheDocument();
    expect(screen.getByText("Upstream")).toBeInTheDocument();
    expect(screen.getByText("web-pool")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  });

  it("renders an absent fact as an em dash, never a blank", () => {
    // A blank value next to a label reads as a rendering bug, not as "none".
    render(
      <RowCards loading={false} isEmpty={false} empty="—">
        <RowCard title="x" facts={[{ label: "Access list", value: null }]} />
      </RowCards>,
    );

    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("makes the whole card pressable when asked", () => {
    // Custom pages open a preview from the row; the card must keep that.
    let pressed = 0;
    render(
      <RowCards loading={false} isEmpty={false} empty="—">
        <RowCard title="Back soon" onPress={() => (pressed += 1)} />
      </RowCards>,
    );

    screen.getByRole("button", { name: /back soon/i }).click();

    expect(pressed).toBe(1);
  });
});
