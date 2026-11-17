import { describe, expect, it } from "vitest";
import { formatBytes, formatDuration, percent, statusLabel, statusTone } from "./format";

describe("format", () => {
  it("formats durations with units", () => {
    expect(formatDuration(0)).toBe("0 秒");
    expect(formatDuration(59.4)).toBe("59 秒");
    expect(formatDuration(90)).toBe("1 分 30 秒");
    expect(formatDuration(3600)).toBe("1 小时 0 分");
    expect(formatDuration(Number.NaN)).toBe("—");
  });

  it("formats bytes", () => {
    expect(formatBytes(0)).toBe("—");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 ** 3)).toBe("5.0 GB");
  });

  it("formats percent with clamp", () => {
    expect(percent(0.456)).toBe("46%");
    expect(percent(2)).toBe("100%");
    expect(percent(-1)).toBe("0%");
    expect(percent(null)).toBe("—");
  });

  it("maps status labels and tones", () => {
    expect(statusLabel("succeeded")).toBe("已完成");
    expect(statusTone("failed")).toBe("danger");
    expect(statusTone("running")).toBe("warn");
    expect(statusTone("ready")).toBe("ok");
    expect(statusTone("weird")).toBe("muted");
  });
});
