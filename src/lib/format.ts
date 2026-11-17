export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const s = Math.round(seconds);
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  const rest = s % 60;
  if (m < 60) return rest ? `${m} 分 ${rest} 秒` : `${m} 分`;
  const h = Math.floor(m / 60);
  return `${h} 小时 ${m % 60} 分`;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

export function formatClock(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

export function percent(ratio: number | null | undefined): string {
  if (ratio == null || !Number.isFinite(ratio)) return "—";
  return `${Math.round(Math.max(0, Math.min(1, ratio)) * 100)}%`;
}

const STATUS_LABELS: Record<string, string> = {
  blocked: "被阻塞",
  queued: "排队中",
  running: "运行中",
  stopping: "正在停止",
  stopped: "已停止",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
  ready: "可用",
  compressed: "已压缩",
  incompatible: "不兼容",
  missing: "文件缺失",
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

export function statusTone(status: string): "ok" | "warn" | "danger" | "muted" {
  if (status === "succeeded" || status === "ready" || status === "pass") return "ok";
  if (status === "failed" || status === "incompatible" || status === "missing" || status === "fail")
    return "danger";
  if (status === "running" || status === "queued" || status === "stopping" || status === "warn")
    return "warn";
  return "muted";
}
