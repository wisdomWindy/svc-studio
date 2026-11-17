import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const consentsStatus = vi.fn();
const acceptConsents = vi.fn();

vi.mock("@/lib/backend", () => ({
  backend: {
    consentsStatus: () => consentsStatus(),
    acceptConsents: (kinds: string[]) => acceptConsents(kinds),
  },
}));

import { ConsentGate } from "./ConsentGate";

describe("ConsentGate", () => {
  beforeEach(() => {
    consentsStatus.mockReset();
    acceptConsents.mockReset();
  });

  it("renders four confirmations and keeps the button disabled", async () => {
    consentsStatus.mockResolvedValue({ accepted: [], complete: false, docVersion: "v1" });
    render(<ConsentGate onComplete={() => undefined} />);

    await waitFor(() => expect(screen.getByText("使用前请确认")).toBeInTheDocument());
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleName("使用前请确认");
    expect(screen.getAllByRole("checkbox")).toHaveLength(4);
    await waitFor(() => expect(screen.getAllByRole("checkbox")[0]).toHaveFocus());
    expect(screen.getByRole("button", { name: "确认并继续" })).toBeDisabled();
  });

  it("stays hidden when consents are complete", async () => {
    consentsStatus.mockResolvedValue({
      accepted: ["agpl", "usage", "dataset", "voice"],
      complete: true,
      docVersion: "v1",
    });
    const { container } = render(<ConsentGate onComplete={() => undefined} />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("calls accept and completes when all checked", async () => {
    consentsStatus.mockResolvedValue({ accepted: [], complete: false, docVersion: "v1" });
    acceptConsents.mockResolvedValue({
      accepted: ["agpl", "usage", "dataset", "voice"],
      complete: true,
      docVersion: "v1",
    });
    const onComplete = vi.fn();
    render(<ConsentGate onComplete={onComplete} />);

    await waitFor(() => expect(screen.getAllByRole("checkbox")).toHaveLength(4));
    for (const box of screen.getAllByRole("checkbox")) box.click();
    const button = screen.getByRole("button", { name: "确认并继续" });
    await waitFor(() => expect(button).toBeEnabled());
    button.click();

    await waitFor(() => expect(onComplete).toHaveBeenCalled());
    expect(acceptConsents).toHaveBeenCalledWith(["agpl", "usage", "dataset", "voice"]);
  });
});
