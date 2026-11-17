import { describe, expect, it } from "vitest";
import { isAppError, isTauri } from "./api";

describe("api guards", () => {
  it("isTauri is false outside Tauri", () => {
    expect(isTauri()).toBe(false);
  });

  it("recognises an AppError shape", () => {
    const err = {
      code: "SVC-2001",
      severity: "blocking",
      message: "缺少权重",
      cause: "未导入 contentvec",
      nextAction: { label: "去资产中心", deeplink: "/settings" },
      detail: null,
      retriable: false,
    };
    expect(isAppError(err)).toBe(true);
    expect(isAppError(new Error("boom"))).toBe(false);
    expect(isAppError(null)).toBe(false);
  });
});
