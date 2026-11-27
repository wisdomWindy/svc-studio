import type { JobEvent } from "@/types";

/**
 * loss 曲线的描边色。
 *
 * 注意：`--accent` 存的是**空格分隔的 RGB 分量**（`214 106 58`），必须用 `rgb(...)` 包起来；
 * 直接写 `stroke="var(--accent)"` 会被解析成 `stroke: 214 106 58`——非法 CSS，浏览器把 stroke
 * 回落成 `none`，于是「面板和数字都在、线却看不见」。
 */
export const LOSS_STROKE = "rgb(var(--accent, 214 106 58))";

/** 训练遥测：从 CURVE 事件里取出耗时与显存，算 ETA。缺字段（旧任务）时返回 null。 */
export interface TrainingTelemetry {
  lastStep: number | null;
  secPerStep: number | null;
  etaSec: number | null;
  vramPeakMb: number | null;
  vramMb: number | null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function parseTrainingTelemetry(events: JobEvent[], total: number | null): TrainingTelemetry {
  let firstStep: number | null = null;
  let firstElapsed: number | null = null;
  let lastStep: number | null = null;
  let lastElapsed: number | null = null;
  let vramPeak: number | null = null;
  let vramNow: number | null = null;

  for (const event of events) {
    if (event.code !== "CURVE" || !event.dataJson) continue;
    let data: { step?: unknown; elapsed?: unknown; vramMb?: unknown; vramPeakMb?: unknown };
    try {
      data = JSON.parse(event.dataJson);
    } catch {
      continue;
    }
    const step = num(data.step);
    const elapsed = num(data.elapsed);
    const peak = num(data.vramPeakMb);
    const current = num(data.vramMb);
    if (peak != null) vramPeak = vramPeak == null ? peak : Math.max(vramPeak, peak);
    if (current != null) vramNow = current;
    if (step == null) continue;
    lastStep = step;
    if (elapsed == null) continue;
    lastElapsed = elapsed;
    if (firstStep == null) {
      firstStep = step;
      firstElapsed = elapsed;
    }
  }

  let secPerStep: number | null = null;
  if (
    lastStep != null &&
    lastElapsed != null &&
    firstStep != null &&
    firstElapsed != null &&
    lastStep > firstStep &&
    lastElapsed > firstElapsed
  ) {
    secPerStep = (lastElapsed - firstElapsed) / (lastStep - firstStep);
  }
  let etaSec: number | null = null;
  if (secPerStep != null && lastStep != null && total != null && total > lastStep) {
    etaSec = (total - lastStep) * secPerStep;
  }
  return {
    lastStep,
    secPerStep,
    etaSec,
    vramPeakMb: vramPeak,
    vramMb: vramNow,
  };
}

export interface LossPoint {
  step: number;
  value: number;
}

/** 只认带 CURVE 标记的事件；坏 JSON 直接跳过，不让一条脏数据毁掉整条曲线。 */
export function parseLossSeries(events: JobEvent[]): LossPoint[] {
  const points: LossPoint[] = [];
  for (const event of events) {
    if (event.code !== "CURVE" || !event.dataJson) continue;
    try {
      const data = JSON.parse(event.dataJson) as { step?: number; losses?: unknown };
      const losses = Array.isArray(data.losses) ? data.losses : [];
      const first = losses.find((v) => typeof v === "number" && Number.isFinite(v));
      if (typeof data.step === "number" && typeof first === "number") {
        points.push({ step: data.step, value: first });
      }
    } catch {
      // 忽略坏样本
    }
  }
  points.sort((a, b) => a.step - b.step);
  return points;
}

/** 生成 SVG 折线路径；点数不足或全部等值时返回空串，避免画出假曲线。 */
export function sparklinePath(points: LossPoint[], width: number, height: number): string {
  if (points.length < 2 || width <= 0 || height <= 0) return "";
  const min = Math.min(...points.map((p) => p.value));
  const max = Math.max(...points.map((p) => p.value));
  if (!Number.isFinite(min) || !Number.isFinite(max)) return "";
  const span = max - min;
  const x = (index: number) => (index / (points.length - 1)) * width;
  const y = (value: number) =>
    span === 0 ? height / 2 : height - ((value - min) / span) * height;
  return points
    .map((point, index) => `${index === 0 ? "M" : "L"}${x(index).toFixed(2)},${y(point.value).toFixed(2)}`)
    .join(" ");
}
