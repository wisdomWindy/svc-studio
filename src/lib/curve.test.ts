import { describe, expect, it } from "vitest";
import { parseLossSeries, sparklinePath } from "./curve";
import type { JobEvent } from "@/types";

function event(seq: number, step: number, losses: number[], code = "CURVE"): JobEvent {
  return {
    seq,
    ts: "2026-01-01T00:00:00Z",
    level: "debug",
    code,
    message: "loss",
    dataJson: JSON.stringify({ step, losses }),
  };
}

describe("loss curve", () => {
  it("parses curve events and sorts by step", () => {
    const points = parseLossSeries([event(1, 3, [0.5, 0.2]), event(2, 1, [0.9, 0.3])]);
    expect(points.map((p) => p.step)).toEqual([1, 3]);
    expect(points[0]?.value).toBe(0.9);
  });

  it("ignores non-curve events and broken json", () => {
    const broken: JobEvent = {
      seq: 3,
      ts: "x",
      level: "debug",
      code: "CURVE",
      message: "loss",
      dataJson: "{not json",
    };
    expect(parseLossSeries([event(1, 1, [1]), broken, { ...event(2, 2, [1]), code: "SVC-5001" }])).toHaveLength(1);
  });

  it("returns empty path when fewer than two points", () => {
    expect(sparklinePath([], 100, 40)).toBe("");
    expect(sparklinePath([{ step: 1, value: 0.5 }], 100, 40)).toBe("");
  });

  it("maps values into the box and keeps flat series centered", () => {
    const rising = sparklinePath(
      [
        { step: 1, value: 1 },
        { step: 2, value: 2 },
      ],
      100,
      40,
    );
    expect(rising.startsWith("M0.00,40.00")).toBe(true);

    const flat = sparklinePath(
      [
        { step: 1, value: 2 },
        { step: 2, value: 2 },
      ],
      100,
      40,
    );
    expect(flat).toContain("20.00");
  });
});
