import type { AppError } from "@/types";

const SEVERITY_LABEL: Record<AppError["severity"], string> = {
  blocking: "无法继续",
  recoverable: "可恢复",
  warning: "注意",
  fatal: "出错了",
};

export interface ErrorNoticeProps {
  error: AppError | string | null;
  onRetry?: () => void;
  compact?: boolean;
}

/**
 * 错误三段式（PRD §8）：现象 → 原因 → 下一步。
 * 缺哪一段就少显示哪一段，不允许只丢一句英文异常给用户。
 */
export function ErrorNotice({ error, onRetry, compact = false }: ErrorNoticeProps) {
  if (!error) return null;

  const structured: AppError | null = typeof error === "string" ? null : error;
  const message = typeof error === "string" ? error : error.message;
  const cause = structured?.cause ?? null;
  const action = structured?.nextAction ?? null;

  return (
    <div
      role="alert"
      className={`rounded-lg border border-danger/40 bg-danger/5 ${compact ? "px-3 py-2" : "p-3.5"}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 text-[12px] text-danger">
          <span className="rounded bg-danger/15 px-1.5 py-0.5 text-[10px]">
            {structured ? SEVERITY_LABEL[structured.severity] : "错误"}
          </span>
          {structured && <span className="font-mono text-[11px] opacity-80">{structured.code}</span>}
        </div>
        {action && (
          <button
            type="button"
            className="shrink-0 rounded border border-danger/40 px-2 py-0.5 text-[11px] text-danger hover:bg-danger/10"
            onClick={() => {
              if (onRetry) onRetry();
            }}
          >
            {action.label}
          </button>
        )}
      </div>

      <p className="mt-1.5 text-[12px] leading-relaxed text-ink">{message}</p>
      {cause && (
        <p className="mt-1 text-[11px] leading-relaxed text-muted">
          <span className="text-muted/80">原因：</span>
          {cause}
        </p>
      )}
      {action && !compact && (
        <p className="mt-1 text-[11px] text-muted">
          <span className="text-muted/80">下一步：</span>
          {action.label}
          {action.deeplink ? `（${action.deeplink}）` : ""}
        </p>
      )}
    </div>
  );
}
