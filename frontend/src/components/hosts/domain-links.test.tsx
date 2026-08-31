import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { DomainLinks } from "@/components/hosts/domain-links";

afterEach(cleanup);

describe("DomainLinks", () => {
  it("links a plain domain over http when there is no certificate", () => {
    render(<DomainLinks domains={["app.example.com"]} secure={false} />);
    // Without a certificate nginx only listens on :80, so https would not answer.
    expect(screen.getByRole("link", { name: "app.example.com" })).toHaveAttribute(
      "href",
      "http://app.example.com",
    );
  });

  it("links over https when a certificate is attached", () => {
    render(<DomainLinks domains={["app.example.com"]} secure />);
    expect(screen.getByRole("link", { name: "app.example.com" })).toHaveAttribute(
      "href",
      "https://app.example.com",
    );
  });

  it("opens in a new tab without handing over window.opener", () => {
    render(<DomainLinks domains={["app.example.com"]} secure />);
    const link = screen.getByRole("link", { name: "app.example.com" });
    expect(link).toHaveAttribute("target", "_blank");
    // Without noopener the opened page can navigate this one elsewhere.
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("renders a wildcard as text, not a link", () => {
    render(<DomainLinks domains={["*.example.com"]} secure />);
    // There is no single address behind a wildcard; inventing one would send
    // the operator somewhere they never configured.
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText("*.example.com")).toBeInTheDocument();
  });

  it("mixes linked and plain entries in one cell", () => {
    render(<DomainLinks domains={["*.example.com", "app.example.com"]} secure />);
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByRole("link", { name: "app.example.com" })).toBeInTheDocument();
    expect(screen.getByText("*.example.com")).toBeInTheDocument();
  });

  it("renders every domain it is given", () => {
    render(<DomainLinks domains={["a.example.com", "b.example.com"]} secure />);
    expect(screen.getAllByRole("link")).toHaveLength(2);
  });
});
