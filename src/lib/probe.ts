/**
 * 主线程响应探针。
 *
 * 前提要说清楚：它测的是"事件循环被占用的程度"，用 `setTimeout(0)` 的实际等待时间
 * 减去理论 0ms 得到延迟样本。UI 卡顿、长任务、同步 IPC 都会体现在这个值上。
 *
 * 它**不等于** G1 要求的"训练满载时"场景：真正满载需要 GPU 训练在跑。
 * 这里能做的是同一套测量方法 + 可选的 IPC 压力（连续调用后端命令）。
 */

export interface ProbeOptions {
  /** 采样时长（毫秒） */
  durationMs?: number;
  /** 采样间隔（毫秒），越小越密 */
  intervalMs?: number;
  /** 每次采样前额外发起一次 IPC（用于施压） */
  stress?: (() => Promise<unknown>) | null;
}

export interface ProbeSample {
  /** 计划触发时间到实际执行时间的偏差（毫秒） */
  delayMs: number;
}

export interface ProbeSummary {
  samples: number;
  p50: number;
  p95: number;
  max: number;
  over200ms: number;
  durationMs: number;
}

export function summarize(samples: number[]): ProbeSummary {
  if (samples.length === 0) {
    return { samples: 0, p50: 0, p95: 0, max: 0, over200ms: 0, durationMs: 0 };
  }
  const sorted = [...samples].sort((a, b) => a - b);
  const at = (q: number) => sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))] ?? 0;
  return {
    samples: sorted.length,
    p50: round(at(0.5)),
    p95: round(at(0.95)),
    max: round(sorted[sorted.length - 1] ?? 0),
    over200ms: samples.filter((v) => v > 200).length,
    durationMs: 0,
  };
}

function round(v: number): number {
  return Math.round(v * 10) / 10;
}

/** 运行探针；返回摘要与原始样本。 */
export async function runProbe(options: ProbeOptions = {}): Promise<ProbeSummary> {
  const durationMs = options.durationMs ?? 5000;
  const intervalMs = options.intervalMs ?? 20;
  const stress = options.stress ?? null;

  const samples: number[] = [];
  const started = performance.now();

  while (performance.now() - started < durationMs) {
    const before = performance.now();
    if (stress) {
      await stress().catch(() => undefined);
    }
    await new Promise<void>((resolve) => setTimeout(resolve, intervalMs));
    // 只记"超出计划间隔的部分"，否则 intervalMs 自身会被算成延迟。
    samples.push(Math.max(0, performance.now() - before - intervalMs));
  }

  const summary = summarize(samples);
  summary.durationMs = round(performance.now() - started);
  return summary;
}
