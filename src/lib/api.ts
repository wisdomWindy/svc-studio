import { invoke } from "@tauri-apps/api/core";
import type { AppError } from "@/types";
import { toErrorText } from "./errors";

/** IPC 的唯一出口：超时、错误归一、dev 日志。业务代码不得直接 invoke。 */
// 0 = 不设超时：长任务（预处理/训练/扫描/QC）跑多久就等多久；
// 需要防挂死的少数调用会显式传 timeoutMs。
const DEFAULT_TIMEOUT_MS = 0;

export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

function toAppError(e: unknown, command: string): AppError {
  if (typeof e === "object" && e !== null && "code" in e && "message" in e) {
    return e as AppError;
  }
  const message = toErrorText(e);
  return {
    code: "SVC-7002",
    severity: "fatal",
    message,
    cause: `调用 ${command} 失败`,
    nextAction: { label: "重试", deeplink: null },
    detail: null,
    retriable: true,
  };
}

export async function call<T>(
  command: string,
  args?: Record<string, unknown>,
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<T> {
  if (!isTauri()) {
    throw toAppError(new Error(`浏览器预览无后端：${command}`), command);
  }
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    let result: T;
    if (timeoutMs > 0) {
      const timeout = new Promise<never>((_, reject) => {
        timer = setTimeout(
          () => reject(new Error(`IPC timeout: ${command} > ${timeoutMs}ms`)),
          timeoutMs,
        );
      });
      result = await Promise.race([invoke<T>(command, args), timeout]);
    } else {
      result = await invoke<T>(command, args);
    }
    if (import.meta.env.DEV) console.debug(`[ipc] ${command}`, result);
    return result;
  } catch (e) {
    const err = toAppError(e, command);
    if (import.meta.env.DEV) console.error(`[ipc] ${command} failed`, err);
    throw err;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export function isAppError(e: unknown): e is AppError {
  return typeof e === "object" && e !== null && "code" in e && "message" in e;
}
