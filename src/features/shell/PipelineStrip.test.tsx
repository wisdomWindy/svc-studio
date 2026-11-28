import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { PipelineStrip } from "@/features/shell/PipelineStrip";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <PipelineStrip />
    </MemoryRouter>,
  );
}

describe("产线导引条", () => {
  it("第一步是创建项目，不是导入", () => {
    renderAt("/projects");
    const steps = screen.getAllByRole("link");
    expect(steps[0]).toHaveTextContent("创建项目");
    expect(steps[1]).toHaveTextContent("导入与处理");
  });

  it("导入与质检/切片是同一步（不再拆成两个）", () => {
    renderAt("/projects");
    const labels = screen.getAllByRole("link").map((el) => el.textContent ?? "");
    expect(labels.some((t) => t.includes("导入与处理"))).toBe(true);
    expect(labels.some((t) => t === "质检" || t.endsWith("质检"))).toBe(false);
    expect(labels.some((t) => t === "切片" || t.endsWith("切片"))).toBe(false);
  });

  it("完整六步顺序与真实流程一致", () => {
    renderAt("/projects");
    const labels = screen.getAllByRole("link").map((el) => el.textContent?.replace(/^\d/, "") ?? "");
    expect(labels).toEqual([
      "创建项目",
      "导入与处理",
      "预处理",
      "训练",
      "登记模型",
      "推理",
    ]);
  });

  it("每一步都能跳到对应页面", () => {
    renderAt("/training");
    const paths = screen.getAllByRole("link").map((el) => el.getAttribute("href"));
    expect(paths).toEqual([
      "/projects",
      "/dataset",
      "/preprocess",
      "/training",
      "/models",
      "/inference",
    ]);
  });

  it("当前页面高亮（aria-current）", () => {
    renderAt("/dataset");
    const active = screen.getAllByRole("link").filter((el) => el.getAttribute("aria-current") === "page");
    expect(active).toHaveLength(1);
    expect(active[0]).toHaveTextContent("导入与处理");
  });
});
