import { describe, expect, it } from "vitest";
import { summarize } from "./probe";

describe("probe summary", () => {
  it("computes percentiles on sorted samples", () => {
    const result = summarize([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect(result.samples).toBe(10);
    expect(result.p50).toBe(6);
    expect(result.p95).toBe(10);
    expect(result.max).toBe(10);
  });

  it("counts samples over the 200ms budget", () => {
    const result = summarize([10, 250, 5, 300]);
    expect(result.over200ms).toBe(2);
  });

  it("handles empty input without dividing by zero", () => {
    expect(summarize([])).toEqual({
      samples: 0,
      p50: 0,
      p95: 0,
      max: 0,
      over200ms: 0,
      durationMs: 0,
    });
  });

  it("keeps one decimal place", () => {
    const result = summarize([1.234, 2.345]);
    expect(result.p50).toBe(2.3);
    expect(result.max).toBe(2.3);
  });
});
