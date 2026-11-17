import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { isTauri } from "./api";
import type { Job, JobEvent } from "@/types";

export interface JobEventPayload {
  jobId: string;
  event?: JobEvent;
  job?: Job;
}

/** 订阅后端任务事件；浏览器预览下返回空解绑函数。 */
export async function subscribeJobEvents(
  handler: (payload: JobEventPayload) => void,
): Promise<() => void> {
  if (!isTauri()) return () => {};
  const unlisteners: UnlistenFn[] = [];
  try {
    unlisteners.push(await listen<JobEventPayload>("job:event", (e) => handler(e.payload)));
    unlisteners.push(await listen<JobEventPayload>("job:done", (e) => handler(e.payload)));
  } catch {
    // 事件通道不可用不应阻断页面
  }
  return () => unlisteners.forEach((fn) => fn());
}
