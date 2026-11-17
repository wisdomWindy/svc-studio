import { useCallback, useState } from "react";
import { isAppError } from "./api";
import type { AppError } from "@/types";

export interface AsyncAction<A extends unknown[], R> {
  run: (...args: A) => Promise<R | undefined>;
  pending: boolean;
  error: string | null;
  appError: AppError | null;
  data: R | null;
  reset: () => void;
}

/** 统一把 IPC 错误转成可读文案与结构化对象，避免每个页面写一遍 try/catch。 */
export function useAsyncAction<A extends unknown[], R>(
  fn: (...args: A) => Promise<R>,
): AsyncAction<A, R> {
  const [pending, setPending] = useState(false);
  const [appError, setAppError] = useState<AppError | null>(null);
  const [data, setData] = useState<R | null>(null);

  const run = useCallback(
    async (...args: A) => {
      setPending(true);
      setAppError(null);
      try {
        const result = await fn(...args);
        setData(result);
        return result;
      } catch (e) {
        if (isAppError(e)) {
          setAppError(e);
        } else {
          setAppError({
            code: "SVC-7002",
            severity: "fatal",
            message: e instanceof Error ? e.message : String(e),
            cause: "前端调用后端命令失败",
            nextAction: { label: "重试", deeplink: null },
            detail: null,
            retriable: true,
          });
        }
        return undefined;
      } finally {
        setPending(false);
      }
    },
    [fn],
  );

  return {
    run,
    pending,
    error: appError?.message ?? null,
    appError,
    data,
    reset: () => setData(null),
  };
}
