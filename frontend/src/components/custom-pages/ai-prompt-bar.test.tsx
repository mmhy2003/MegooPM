import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
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

  it("submits on Enter, since it is a one-line instruction", async () => {
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

    await user.type(screen.getByLabelText("Instruction"), "tidy it{Enter}");
    expect(onSubmit).toHaveBeenCalledWith("tidy it");
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
