import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { CountryFlag } from "@/components/ui/country-flag";

afterEach(cleanup);

describe("CountryFlag", () => {
  it("renders the sprite class with the code as its accessible name", () => {
    render(<CountryFlag country="de" />);
    const flag = screen.getByRole("img", { name: "DE" });
    expect(flag).toHaveClass("fi", "fi-de");
    expect(flag).toHaveAttribute("title", "DE");
  });

  it("renders nothing for an unknown country", () => {
    const { container } = render(
      <>
        <CountryFlag country={null} />
        <CountryFlag country={undefined} />
        <CountryFlag country="" />
        <CountryFlag country="XYZ" />
      </>,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
