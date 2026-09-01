import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { AuthGuard } from "@/components/auth-guard";

const replace = vi.fn();
let status = "loading";

vi.mock("next/navigation", () => ({
  usePathname: () => "/hosts",
  useRouter: () => ({ replace }),
}));
vi.mock("@/lib/auth/context", () => ({
  useAuth: () => ({ status }),
}));
// next/image needs no loader in jsdom; render the plain tag.
vi.mock("next/image", () => ({
  default: ({ alt, src }: { alt: string; src: string }) => (
    // eslint-disable-next-line @next/next/no-img-element
    <img alt={alt} src={src} />
  ),
}));

describe("AuthGuard", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    status = "loading";
  });

  it("still announces the wait once the visible text is a logo", async () => {
    // A live region announces its text CONTENT (role=status takes no name from
    // its contents), so with the text replaced by an image it would announce
    // nothing and a screen reader user would get silence instead of "Loading".
    status = "loading";
    render(
      <AuthGuard>
        <p>secret</p>
      </AuthGuard>,
    );

    expect(await screen.findByRole("status")).toHaveTextContent(/loading/i);
  });

  it("shows the app logo while the session is unknown", async () => {
    status = "loading";
    render(
      <AuthGuard>
        <p>secret</p>
      </AuthGuard>,
    );

    expect(await screen.findByAltText(/megoopm logo/i)).toBeInTheDocument();
  });

  it("withholds the app shell until the session is known", () => {
    status = "loading";
    render(
      <AuthGuard>
        <p>secret</p>
      </AuthGuard>,
    );

    expect(screen.queryByText("secret")).not.toBeInTheDocument();
  });

  it("renders the app once authenticated, with no spinner left behind", () => {
    status = "authenticated";
    render(
      <AuthGuard>
        <p>secret</p>
      </AuthGuard>,
    );

    expect(screen.getByText("secret")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("sends an unauthenticated visitor to login, keeping where they were going", () => {
    status = "unauthenticated";
    render(
      <AuthGuard>
        <p>secret</p>
      </AuthGuard>,
    );

    const url = replace.mock.calls[0][0] as string;
    const [route, query] = url.split("?");
    expect(route).toBe("/login");
    // Encoded in the URL, so compare the decoded value rather than a substring.
    expect(new URLSearchParams(query).get("next")).toBe("/hosts");
  });
});
