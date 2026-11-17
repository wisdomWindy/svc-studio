import type { JobEvent } from "@/types";

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
