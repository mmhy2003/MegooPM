import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CHECK_EVERY_MS, CONFIRM_AFTER_MS, UpdateNotice } from "@/components/update-notice";

/** What /version.json answers; `null` makes the request fail. */
let served: string | null;
const fetchMock = vi.fn(async () => {
  if (served === null) throw new TypeError("Failed to fetch");
  return new Response(JSON.stringify({ id: served }), { status: 200 });
});

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubEnv("NEXT_DEPLOYMENT_ID", "old");
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockClear();
  served = "old";
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

/** Lets the pending check (and any timers due within `ms`) finish. */
async function wait(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

const notice = () => screen.queryByText("MegooPM was updated");

function comeBack() {
  Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
  fireEvent(document, new Event("visibilitychange"));
}

describe("UpdateNotice", () => {
  it("shows nothing while the server runs the same build as the page", async () => {
    render(<UpdateNotice />);
    await wait(CHECK_EVERY_MS * 2);
    expect(fetchMock).toHaveBeenCalled();
    expect(notice()).toBeNull();
  });

  it("asks for a reload once two checks in a row see a newer build", async () => {
    served = "new";
    render(<UpdateNotice />);
    await wait();
    // One sighting is not enough: a rolling update can still answer from an old node.
    expect(notice()).toBeNull();
    await wait(CONFIRM_AFTER_MS);
    expect(notice()).toBeInTheDocument();
    expect(screen.getByText(/Save anything you're editing first/)).toBeInTheDocument();
  });

  it("does not ask when the newer build was seen only once", async () => {
    render(<UpdateNotice />);
    await wait();
    served = "new";
    comeBack();
    await wait();
    served = "old";
    await wait(CONFIRM_AFTER_MS);
    expect(notice()).toBeNull();
  });

  it("reloads the page from Reload", async () => {
    served = "new";
    const reload = vi.fn();
    render(<UpdateNotice reload={reload} />);
    await wait(CONFIRM_AFTER_MS);
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("never reloads by itself", async () => {
    served = "new";
    const reload = vi.fn();
    render(<UpdateNotice reload={reload} />);
    await wait(CHECK_EVERY_MS * 3);
    expect(notice()).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
  });

  it("hides on Later until a still newer build appears", async () => {
    served = "new";
    render(<UpdateNotice />);
    await wait(CONFIRM_AFTER_MS);
    fireEvent.click(screen.getByRole("button", { name: "Later" }));
    expect(notice()).toBeNull();

    await wait(CHECK_EVERY_MS * 2);
    expect(notice()).toBeNull();

    served = "newer";
    await wait(CHECK_EVERY_MS + CONFIRM_AFTER_MS);
    expect(notice()).toBeInTheDocument();
  });

  it("checks again when the person comes back to the tab", async () => {
    render(<UpdateNotice />);
    await wait();
    const before = fetchMock.mock.calls.length;
    comeBack();
    await wait();
    expect(fetchMock.mock.calls.length).toBe(before + 1);
  });

  it("checks again right after a part of the app fails to load", async () => {
    render(<UpdateNotice />);
    await wait();
    const before = fetchMock.mock.calls.length;
    const failed = new Error("Loading chunk 42 failed.");
    failed.name = "ChunkLoadError";
    const event = new Event("unhandledrejection") as PromiseRejectionEvent;
    Object.defineProperty(event, "reason", { value: failed });
    fireEvent(window, event);
    await wait();
    expect(fetchMock.mock.calls.length).toBe(before + 1);
  });

  it("stays quiet when the server cannot be reached", async () => {
    served = null;
    render(<UpdateNotice />);
    await wait(CHECK_EVERY_MS * 2);
    expect(notice()).toBeNull();
  });

  it("does nothing in a build without an ID, such as the dev server", async () => {
    vi.stubEnv("NEXT_DEPLOYMENT_ID", "");
    render(<UpdateNotice />);
    await wait(CHECK_EVERY_MS * 2);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
