import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AiPromptBar } from "@/components/custom-pages/ai-prompt-bar";

afterEach(cleanup);

describe("AiPromptBar", () => {
  it("submits the instruction", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(
      <AiPromptBar
        enabled
        busy={false}
        elapsedSeconds={0}
        onSubmit={onSubmit}
        onCancel={() => {}}
      />,
    );

    await user.type(screen.getByLabelText("Instruction"), "make the heading bigger");
    await user.click(screen.getByRole("button", { name: "Generate" }));

    expect(onSubmit).toHaveBeenCalledWith("make the heading bigger");
  });

  it("keeps Enter for a new line and sends on Ctrl+Enter", async () => {
    // The instruction is a paragraph, not a search box: Enter has to be able
    // to break a line, so sending needs its own chord.
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(
      <AiPromptBar
        enabled
        busy={false}
        elapsedSeconds={0}
        onSubmit={onSubmit}
        onCancel={() => {}}
      />,
    );

    const box = screen.getByLabelText("Instruction");
    await user.type(box, "first line{Enter}second line");
    expect(onSubmit).not.toHaveBeenCalled();
    expect(box).toHaveValue("first line\nsecond line");

    await user.type(box, "{Control>}{Enter}{/Control}");
    expect(onSubmit).toHaveBeenCalledWith("first line\nsecond line");
  });

  it("empties itself once the run finishes, ready for the next instruction", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(true);
    render(
      <AiPromptBar
        enabled
        busy={false}
        elapsedSeconds={0}
        onSubmit={onSubmit}
        onCancel={() => {}}
      />,
    );

    const box = screen.getByLabelText("Instruction");
    await user.type(box, "make the heading bigger");
    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(box).toHaveValue(""));
  });

  it("keeps the instruction when the run fails", async () => {
    // Losing a carefully written paragraph to a timeout would make the
    // operator retype it to retry.
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(false);
    render(
      <AiPromptBar
        enabled
        busy={false}
        elapsedSeconds={0}
        onSubmit={onSubmit}
        onCancel={() => {}}
      />,
    );

    const box = screen.getByLabelText("Instruction");
    await user.type(box, "make the heading bigger");
    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(box).toHaveValue("make the heading bigger");
  });

  it("refuses to submit nothing", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(
      <AiPromptBar
        enabled
        busy={false}
        elapsedSeconds={0}
        onSubmit={onSubmit}
        onCancel={() => {}}
      />,
    );

    await user.type(screen.getByLabelText("Instruction"), "   ");
    await user.click(screen.getByRole("button", { name: "Generate" }));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("shows elapsed time and a cancel while busy", async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(
      <AiPromptBar
        enabled
        busy
        elapsedSeconds={14}
        onSubmit={() => {}}
        onCancel={onCancel}
      />,
    );

    expect(screen.getByText(/14s/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Generate" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("points at Settings when LLM features are off", () => {
    render(
      <AiPromptBar
        enabled={false}
        busy={false}
        elapsedSeconds={0}
        onSubmit={() => {}}
        onCancel={() => {}}
      />,
    );

    expect(screen.getByText(/enable llm features/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /settings/i })).toHaveAttribute("href", "/settings");
    expect(screen.queryByLabelText("Instruction")).not.toBeInTheDocument();
  });
});
