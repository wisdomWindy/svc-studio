import { describe, expect, it } from "vitest";
import { LOSS_STROKE, parseLossSeries, sparklinePath } from "./curve";
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
