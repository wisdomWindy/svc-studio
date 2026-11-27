import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { isActive, jobPercent } from "@/features/projects/OverviewPage";
import { compatBadge } from "@/features/models/ModelsPage";
import type { Job } from "@/types";

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: "job_1",
    type: "train_main",
    projectId: "prj_1",
    status: "running",
    progress: 0,
    step: null,
    total: null,
    errorCode: null,
    startedAt: "2026-11-27T12:00:00Z",
    finishedAt: null,
    ...overrides,
  };
}

describe("项目卡进度", () => {
  it("running / queued 算活动任务，其余不算", () => {
    expect(isActive(job())).toBe(true);
    expect(isActive(job({ status: "queued" }))).toBe(true);
    expect(isActive(job({ status: "succeeded" }))).toBe(false);
    expect(isActive(job({ status: "failed" }))).toBe(false);
  });

  it("有 step/total 时按步数算百分比（训练）", () => {
    expect(jobPercent(job({ step: 12400, total: 40000, progress: 99 }))).toBe(31);
  });

  it("没有步数时退回 progress 字段", () => {
    expect(jobPercent(job({ progress: 42 }))).toBe(42);
  });

  it("越界值被夹在 0–100", () => {
    expect(jobPercent(job({ progress: 130 }))).toBe(100);
    expect(jobPercent(job({ progress: -5 }))).toBe(0);
    expect(jobPercent(job({ step: 50, total: 10 }))).toBe(100);
  });
});

describe("模型兼容性徽章", () => {
  it("ready → 兼容 · 全部键命中", () => {
    const { container } = render(<>{compatBadge('{"verdict":"ready"}')}</>);
    expect(container.textContent).toContain("兼容");
    expect(container.textContent).toContain("全部键命中");
  });

  it("incompatible → 不兼容 + 缺失项数量", () => {
    const { container } = render(
      <>{compatBadge('{"verdict":"incompatible","missingKeys":["enc_q","diff"]}')}</>,
    );
    expect(container.textContent).toContain("不兼容");
    expect(container.textContent).toContain("2");
  });

  it("unknown → 未知（不冒充可用）", () => {
    const { container } = render(<>{compatBadge('{"verdict":"unknown"}')}</>);
    expect(container.textContent).toContain("未知");
  });

  it("坏 JSON → 报告损坏，而不是崩", () => {
    const { container } = render(<>{compatBadge("{not json")}</>);
    expect(container.textContent).toContain("损坏");
  });
});
