import { describe, expect, it } from "vitest";
import { LOSS_STROKE, parseLossSeries, parseTrainingTelemetry, sparklinePath } from "./curve";
import type { JobEvent } from "@/types";

function curveEvent(step: number, losses: unknown[], seq = step): JobEvent {
  return {
    seq,
    ts: "2026-11-26T12:00:00.000Z",
    level: "info",
    code: "CURVE",
    message: "loss",
    dataJson: JSON.stringify({ step, losses, lr: 0.0001 }),
  };
}

describe("loss 曲线", () => {
  it("从 CURVE 事件解析出 step/loss 序列并按步数排序", () => {
    const points = parseLossSeries([
      curveEvent(400, [1.5, 2, 3]),
      curveEvent(0, [6.07, 5.3]),
      curveEvent(200, [1.42]),
    ]);
    expect(points).toEqual([
      { step: 0, value: 6.07 },
      { step: 200, value: 1.42 },
      { step: 400, value: 1.5 },
    ]);
  });

  it("忽略非 CURVE 事件与坏 JSON", () => {
    const bad: JobEvent = {
      seq: 9,
      ts: "2026-11-26T12:00:00.000Z",
      level: "info",
      code: "CURVE",
      message: "x",
      dataJson: "{not json",
    };
    const other = { ...bad, code: "SVC-5002", dataJson: '{"step":1,"losses":[1]}' };
    expect(parseLossSeries([bad, other])).toEqual([]);
  });

  it("生成可用的 SVG 路径；点数不足时返回空串", () => {
    expect(sparklinePath([{ step: 0, value: 1 }], 520, 96)).toBe("");
    const path = sparklinePath(
      [
        { step: 0, value: 6.07 },
        { step: 9600, value: 1.9 },
      ],
      520,
      96,
    );
    expect(path.startsWith("M")).toBe(true);
    expect(path).not.toContain("NaN");
  });

  it("描边色必须包在 rgb() 里——--accent 是空格分隔的 RGB 分量", () => {
    // 直接写 var(--accent) 会变成 `stroke: 214 106 58`（非法），浏览器回落到 none，线就消失了
    expect(LOSS_STROKE.startsWith("rgb(")).toBe(true);
    expect(LOSS_STROKE).toContain("var(--accent");
    expect(LOSS_STROKE).not.toMatch(/^var\(/);
  });
});

describe("训练遥测（ETA / 显存）", () => {
  const withTelemetry = (step: number, elapsed: number, vramPeakMb?: number, seq = step): JobEvent => ({
    seq,
    ts: "2026-11-27T12:00:00.000Z",
    level: "debug",
    code: "CURVE",
    message: "loss",
    dataJson: JSON.stringify({ step, losses: [1.0], lr: 0.0001, elapsed, vramMb: 5000, vramPeakMb }),
  });

  it("由两个采样点算出每步耗时与剩余时间", () => {
    // 100 步用了 200 秒 → 2 s/步；总 1000 步 → 剩余 900 步 = 1800 秒
    const t = parseTrainingTelemetry(
      [withTelemetry(1000, 100), withTelemetry(1100, 300)],
      2000,
    );
    expect(t.secPerStep).toBeCloseTo(2.0, 3);
    expect(t.etaSec).toBeCloseTo(1800, 1);
    expect(t.lastStep).toBe(1100);
  });

  it("只有一个采样点时只有 s/步为空、不瞎算 ETA", () => {
    const t = parseTrainingTelemetry([withTelemetry(500, 60)], 1000);
    expect(t.secPerStep).toBeNull();
    expect(t.etaSec).toBeNull();
  });

  it("显存峰值取最大值，当前值取最后一个", () => {
    const t = parseTrainingTelemetry(
      [withTelemetry(100, 10, 5000), withTelemetry(200, 20, 6100), withTelemetry(300, 30, 5800)],
      1000,
    );
    expect(t.vramPeakMb).toBe(6100);
    expect(t.vramMb).toBe(5000);
  });

  it("旧任务（无 elapsed/vram）返回空值，不抛错", () => {
    const old = { ...withTelemetry(100, 10) };
    old.dataJson = JSON.stringify({ step: 100, losses: [1.0] });
    const t = parseTrainingTelemetry([old], 1000);
    expect(t.secPerStep).toBeNull();
    expect(t.etaSec).toBeNull();
    expect(t.vramPeakMb).toBeNull();
  });
});
