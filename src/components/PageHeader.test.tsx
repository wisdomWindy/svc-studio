import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PageHeader } from "./PageHeader";

describe("PageHeader", () => {
  it("renders title, description and actions", () => {
    render(
      <PageHeader
        title="数据集"
        description="导入与质检"
        actions={<button type="button">导入</button>}
      />,
    );
    expect(screen.getByRole("heading", { level: 1, name: "数据集" })).toBeInTheDocument();
    expect(screen.getByText("导入与质检")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "导入" })).toBeInTheDocument();
  });
});
