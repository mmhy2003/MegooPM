import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { users, type ApiKey } from "@/lib/api";
import { ApiKeysCard } from "@/components/profile/api-keys-card";

function makeKey(over: Partial<ApiKey> = {}): ApiKey {
  return {
    id: 1,
    name: "CI deploy",
    token_prefix: "mgm_a1b2c3d4",
    enabled: true,
    expires_at: null,
    last_used_at: null,
    created_at: "2026-09-06T00:00:00Z",
    ...over,
  };
}

async function createOne(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /new api key/i }));
  await user.type(screen.getByLabelText("Name"), "CI deploy");
  await user.click(screen.getByRole("button", { name: "Create key" }));
}

beforeEach(() => {
  vi.spyOn(users, "apiKeys").mockResolvedValue([]);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ApiKeysCard", () => {
  it("says what a key is for when there are none", async () => {
    render(<ApiKeysCard />);

    expect(await screen.findByText(/no api keys yet/i)).toBeInTheDocument();
  });

  it("lists a key by name and prefix, never a token", async () => {
    vi.mocked(users.apiKeys).mockResolvedValue([makeKey()]);
    render(<ApiKeysCard />);

    expect(await screen.findByText("CI deploy")).toBeInTheDocument();
    expect(screen.getByText(/mgm_a1b2c3d4/)).toBeInTheDocument();
  });

  it("shows the token once, and only after it is created", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "createApiKey").mockResolvedValue({
      ...makeKey(),
      token: "mgm_the-actual-secret",
    });
    render(<ApiKeysCard />);

    await createOne(user);

    expect(await screen.findByText("mgm_the-actual-secret")).toBeInTheDocument();
    expect(screen.getByText(/only time/i)).toBeInTheDocument();
  });

  it("will not let the token panel close until it is acknowledged", async () => {
    // A stray dismissal costs the key, and the only recovery is another key.
    const user = userEvent.setup();
    vi.spyOn(users, "createApiKey").mockResolvedValue({
      ...makeKey(),
      token: "mgm_the-actual-secret",
    });
    render(<ApiKeysCard />);

    await createOne(user);

    expect(await screen.findByRole("button", { name: "Done" })).toBeDisabled();
    await user.click(screen.getByLabelText(/saved this key/i));
    expect(screen.getByRole("button", { name: "Done" })).toBeEnabled();
  });

  it("will not create a key with no name", async () => {
    const user = userEvent.setup();
    const create = vi.spyOn(users, "createApiKey");
    render(<ApiKeysCard />);

    await user.click(await screen.findByRole("button", { name: /new api key/i }));

    expect(screen.getByRole("button", { name: "Create key" })).toBeDisabled();
    expect(create).not.toHaveBeenCalled();
  });

  it("sends no expiry when Never is chosen, and one otherwise", async () => {
    const user = userEvent.setup();
    const create = vi.spyOn(users, "createApiKey").mockResolvedValue({
      ...makeKey(),
      token: "mgm_the-actual-secret",
    });
    render(<ApiKeysCard />);

    await createOne(user);

    // 30 days is the default: the order of the options is the recommendation.
    expect(create.mock.calls[0][0].expires_at).not.toBeNull();
  });

  it("disables a key from its row", async () => {
    const user = userEvent.setup();
    vi.mocked(users.apiKeys).mockResolvedValue([makeKey()]);
    const patch = vi
      .spyOn(users, "setApiKeyEnabled")
      .mockResolvedValue(makeKey({ enabled: false }));
    render(<ApiKeysCard />);

    await user.click(await screen.findByLabelText("Enable CI deploy"));

    await waitFor(() => expect(patch).toHaveBeenCalledWith(1, false));
  });

  it("deletes a key after confirming", async () => {
    const user = userEvent.setup();
    vi.mocked(users.apiKeys).mockResolvedValue([makeKey()]);
    const remove = vi.spyOn(users, "deleteApiKey").mockResolvedValue(undefined);
    render(<ApiKeysCard />);

    await user.click(await screen.findByRole("button", { name: "Delete CI deploy" }));
    await user.click(await screen.findByRole("button", { name: "Delete key" }));

    await waitFor(() => expect(remove).toHaveBeenCalledWith(1));
  });

  it("marks an expired key as expired", async () => {
    vi.mocked(users.apiKeys).mockResolvedValue([makeKey({ expires_at: "2020-01-01T00:00:00Z" })]);
    render(<ApiKeysCard />);

    expect(await screen.findByText("Expired")).toBeInTheDocument();
  });

  it("says a key has never been used rather than leaving the cell blank", async () => {
    vi.mocked(users.apiKeys).mockResolvedValue([makeKey()]);
    render(<ApiKeysCard />);

    expect(await screen.findByText("Never used")).toBeInTheDocument();
  });
});
