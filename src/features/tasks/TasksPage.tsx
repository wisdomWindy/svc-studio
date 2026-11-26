import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/Button";
import { backend } from "@/lib/backend";
import { formatClock, percent, statusLabel, statusTone } from "@/lib/format";
import { useActiveProject } from "@/lib/store";
import type { Job, JobEvent } from "@/types";
import { toErrorText } from "@/lib/errors";

const TONE: Record<string, string> = {
  ok: "text-ok",
  warn: "text-warn",
  danger: "text-danger",
  muted: "text-muted",
};

const LEVEL_TONE: Record<string, string> = {
  error: "text-danger",
  warn: "text-warn",
  info: "text-ink",
  debug: "text-muted",
};

/** 运行中/排队中的任务不允许删除，也不允许勾选。 */
function isActive(job: Job): boolean {
  return job.status === "running" || job.status === "queued";
}

export function TasksPage() {
  const project = useActiveProject();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [checked, setChecked] = useState<string[]>([]);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  /** 轮询与删除/重试都会发 list_jobs；只认最新一次请求，避免旧快照把已删的行刷回界面。 */
  const seq = useRef(0);
  const projectId = project?.id ?? null;

  /**
   * 拉一次任务列表并按当前项目过滤。
   * 返回 null 表示这份结果已被更晚发出的请求取代，调用方应丢弃它。
   */
  const refresh = useCallback(async (): Promise<Job[] | null> => {
    const mine = ++seq.current;
    const all = await backend.listJobs(100);
    if (mine !== seq.current) return null;
    const list = projectId ? all.filter((j) => j.projectId === projectId) : all;
    setJobs(list);
    return list;
  }, [projectId]);

  useEffect(() => {
    async function tick() {
      try {
        const list = await refresh();
        if (!list) return;
        // 选中的任务被删掉后自动落回运行中的任务，不指向已不存在的记录
        setSelected((prev) =>
          prev && list.some((j) => j.id === prev) ? prev : (list.find(isActive)?.id ?? null),
        );
      } catch (e) {
        setError(toErrorText(e));
      }
    }
    void tick();
    timer.current = setInterval(() => void tick(), 3000);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [refresh]);

  useEffect(() => {
    if (!selected) {
      // 选中项被清除（含删除后）时同步清空日志：否则已删任务的记录会留在右侧面板上，
      // 看起来像「删掉的记录又出现了」。
      setEvents([]);
      return;
    }
    let alive = true;
    async function load() {
      try {
        const page = await backend.jobEvents(selected!, 0, 500);
        if (alive) setEvents(page.events);
      } catch {
        /* 事件通道失败不阻塞列表 */
      }
    }
    void load();
    const id = setInterval(() => void load(), 2000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [selected]);

  const deletable = jobs.filter((j) => !isActive(j));

  async function removeJobs(ids: string[]) {
    if (ids.length === 0) return;
    const targets = jobs.filter((j) => ids.includes(j.id));
    if (targets.some(isActive)) {
      setError("运行中的任务不能删除，请先取消");
      return;
    }
    // 删除推理任务会连音频与报告一起删掉，先确认；纯记录删除没有这一步
    if (targets.some((j) => j.type === "inference")) {
      const tip =
        ids.length === 1
          ? "删除这条推理任务？该次推理的音频与报告会一并删除，不可恢复。"
          : `删除选中的 ${ids.length} 条任务？其中推理任务的音频与报告会一并删除，不可恢复。`;
      if (!window.confirm(tip)) return;
    }
    try {
      const res = await backend.deleteJobs(ids);
      // 运行中被拒、产物删不掉的都保留在列表里，只有真正删掉的才本地移除
      const kept = new Set([...res.refused, ...res.outputFailed]);
      const gone = ids.filter((id) => !kept.has(id));
      setError(null);
      setJobs((prev) => prev.filter((j) => !gone.includes(j.id)));
      setChecked((prev) => prev.filter((id) => !gone.includes(id)));
      if (selected && gone.includes(selected)) setSelected(null);
      const list = await refresh();
      if (list && res.outputFailed.length > 0) {
        setError(
          `有 ${res.outputFailed.length} 个任务的产物删不掉（可能被占用），记录已保留，请关闭占用后重试`,
        );
      } else if (list && res.refused.length > 0) {
        setError(`有 ${res.refused.length} 个运行中的任务未删除`);
      } else if (list && res.deleted === 0) {
        setError("任务已不存在，列表已同步");
      }
    } catch (e) {
      setError(toErrorText(e));
    }
  }

  async function cancel(job: Job) {
    try {
      await backend.cancelJob(job.id);
      setError(null);
    } catch (e) {
      setError(toErrorText(e));
    }
  }

  return (
    <>
      <PageHeader
        title="任务"
        badge={
          <span className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted">
            {jobs.length} 条
          </span>
        }
      />

      {error && <ErrorNotice error={error} compact />}

      <div className="grid gap-3 lg:grid-cols-[1.2fr_1fr]">
        <div className="space-y-2">
          {checked.length > 0 && (
            <div className="flex items-center justify-between rounded-lg border border-line bg-surface px-3 py-1.5 text-[12px]">
              <span className="text-muted">已选 {checked.length} 项</span>
              <Button size="sm" variant="ghost" onClick={() => void removeJobs(checked)}>
                批量删除
              </Button>
            </div>
          )}
        <div className="overflow-hidden rounded-lg border border-line">
          <table className="w-full text-left text-[12px]">
            <thead className="bg-raised text-muted">
              <tr>
                <th className="w-8 px-2 py-2">
                  <input
                    type="checkbox"
                    aria-label="全选"
                    checked={deletable.length > 0 && checked.length === deletable.length}
                    onChange={(e) => setChecked(e.target.checked ? deletable.map((j) => j.id) : [])}
                  />
                </th>
                {["类型", "状态", "进度", "开始", ""].map((h) => (
                  <th key={h} className="px-3 py-2 font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {jobs.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-10 text-center text-muted">
                    暂无任务
                  </td>
                </tr>
              )}
              {jobs.map((job) => (
                <tr
                  key={job.id}
                  onClick={() => setSelected(job.id)}
                  className={`cursor-pointer border-t border-line/60 ${
                    selected === job.id ? "bg-raised/60" : "hover:bg-raised/40"
                  }`}
                >
                  <td className="px-2 py-1.5">
                    <input
                      type="checkbox"
                      aria-label={`选择 ${job.id}`}
                      disabled={isActive(job)}
                      checked={checked.includes(job.id)}
                      onClick={(e) => e.stopPropagation()}
                      onChange={(e) =>
                        setChecked((prev) =>
                          e.target.checked ? [...prev, job.id] : prev.filter((x) => x !== job.id),
                        )
                      }
                    />
                  </td>
                  <td className="px-3 py-1.5">{job.type}</td>
                  <td className={`px-3 py-1.5 ${TONE[statusTone(job.status)]}`}>
                    {statusLabel(job.status)}
                    {job.errorCode ? ` · ${job.errorCode}` : ""}
                  </td>
                  <td className="px-3 py-1.5 tabular">{percent(job.progress)}</td>
                  <td className="px-3 py-1.5 tabular text-muted">{formatClock(job.startedAt)}</td>
                  <td className="px-3 py-1.5 text-right">
                    {job.status === "succeeded" && job.type === "inference" && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={(e) => {
                          e.stopPropagation();
                          void backend
                            .jobOpenFolder(job.id)
                            .then(() => setError(null))
                            .catch((err) => setError(toErrorText(err)));
                        }}
                      >
                        打开文件夹
                      </Button>
                    )}
                    {(job.status === "running" || job.status === "queued") && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={(e) => {
                          e.stopPropagation();
                          void cancel(job);
                        }}
                      >
                        取消
                      </Button>
                    )}
                    {["failed", "interrupted", "cancelled", "stopped"].includes(job.status) && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={(e) => {
                          e.stopPropagation();
                          void backend
                            .retryJob(job.id)
                            .then(() => {
                              setError(null);
                              void refresh().catch(() => undefined);
                            })
                            .catch((err) =>
                              setError(toErrorText(err)),
                            );
                        }}
                      >
                        重试
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={isActive(job)}
                      onClick={(e) => {
                        e.stopPropagation();
                        void removeJobs([job.id]);
                      }}
                    >
                      删除
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </div>

        <div className="rounded-lg border border-line bg-surface">
          <div className="border-b border-line px-3 py-2 text-[12px] text-muted">
            {selected ? `事件 · ${selected}` : "选择左侧任务查看日志"}
          </div>
          <div className="max-h-[420px] overflow-y-auto px-3 py-2 font-mono text-[11px] leading-relaxed">
            {events.length === 0 && <p className="text-muted">暂无事件</p>}
            {[...events].sort((a, b) => b.seq - a.seq).map((event) => (
              <div key={event.seq} className="flex gap-2">
                <span className="shrink-0 text-muted/70 tabular">{formatClock(event.ts)}</span>
                <span className={`shrink-0 ${LEVEL_TONE[event.level] ?? "text-ink"}`}>
                  {event.code ?? event.level}
                </span>
                <span className="min-w-0 break-words">{event.message}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}
