import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ErrorNotice } from "./ErrorNotice";

describe("ErrorNotice", () => {
  it("renders all three parts for a structured error", () => {
    render(
      <ErrorNotice
        error={{
          code: "SVC-5001",
          severity: "blocking",
          message: "显存不足",
          cause: "batch_size=6 超过 6GB 承载",
          nextAction: { label: "自动降档并重试", deeplink: "/training" },
          detail: null,
          retriable: true,
        }}
      />,
    );
    expect(screen.getByText("显存不足")).toBeInTheDocument();
    expect(screen.getByText(/batch_size=6/)).toBeInTheDocument();
    expect(screen.getByText(/下一步/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "自动降档并重试" })).toBeInTheDocument();
    expect(screen.getByText("SVC-5001")).toBeInTheDocument();
  });

  it("renders a plain string without inventing a cause", () => {
    render(<ErrorNotice error="网络未启用" />);
    expect(screen.getByText("网络未启用")).toBeInTheDocument();
    expect(screen.queryByText(/原因/)).not.toBeInTheDocument();
  });

  it("renders nothing for null", () => {
    const { container } = render(<ErrorNotice error={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("calls retry when the action is clicked", () => {
    const onRetry = vi.fn();
    render(
      <ErrorNotice
        error={{
          code: "SVC-7002",
          severity: "recoverable",
          message: "任务中断",
          cause: "",
          nextAction: { label: "重试", deeplink: null },
          detail: null,
          retriable: true,
        }}
        onRetry={onRetry}
      />,
    );
    screen.getByRole("button", { name: "重试" }).click();
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
