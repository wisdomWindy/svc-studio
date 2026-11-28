import { describe, expect, it } from "vitest";
import { datasetStateText, gateHeadline, prepareButtonLabel } from "@/lib/datasetState";

describe("数据集处理状态", () => {
  it("没处理过：提示导入后会自动跑一次，之后只做质检", () => {
    const text = datasetStateText(null);
    expect(text).toContain("尚未处理");
    expect(text).toContain("只做质检");
  });

  it("已处理且源文件没变：明确说不会重复分离/切片", () => {
    const text = datasetStateText({
      preparedAt: "2026-11-28T10:30:00",
      keptCount: 128,
      upToDate: true,
    });
    expect(text).toContain("已处理（2026-11-28 10:30:00）");
    expect(text).toContain("保留 128 段");
    expect(text).toContain("不会重复分离/切片");
  });

  it("已处理但有新增文件：提示只处理新文件", () => {
    const text = datasetStateText({
      preparedAt: "2026-11-28T10:30:00",
      keptCount: null,
      upToDate: false,
    });
    expect(text).toContain("新增源文件");
    expect(text).toContain("只处理这些新文件");
  });

  it("按钮文案随状态变化", () => {
    expect(prepareButtonLabel(null, false)).toBe("开始处理数据集");
    expect(prepareButtonLabel({ preparedAt: "x", keptCount: 1, upToDate: true }, false)).toBe(
      "重新处理（仅新文件）",
    );
    expect(prepareButtonLabel({ preparedAt: "x", keptCount: 1, upToDate: true }, true)).toBe(
      "处理中…",
    );
  });
});

describe("门禁结论标题", () => {
  it("通过且无提示 → 通过", () => {
    expect(gateHeadline({ passed: true, blocking: [], warnings: [] })).toBe("通过");
  });

  it("通过但有提示 → 通过 · N 条提示（不能只写通过）", () => {
    expect(gateHeadline({ passed: true, blocking: [], warnings: [{}, {}] })).toBe(
      "通过 · 2 条提示",
    );
  });

  it("不通过 → 阻断项数 + 提示数", () => {
    expect(gateHeadline({ passed: false, blocking: [{}], warnings: [{}] })).toBe(
      "1 个阻断项 · 1 条提示",
    );
    expect(gateHeadline({ passed: false, blocking: [{}, {}], warnings: [] })).toBe("2 个阻断项");
  });
});