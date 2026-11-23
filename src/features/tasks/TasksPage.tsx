import { useEffect, useRef, useState } from "react";
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

/** 产物已被删除的任务不再显示「打开文件夹 / 删除」。 */
function hasDeletedOutput(job: Job): boolean {
  if (!job.payloadJson) return false;
  try {
    const payload = JSON.parse(job.payloadJson) as { outputDeleted?: boolean };
    return payload.outputDeleted === true;
  } catch {
    return false;
  }
}

export function TasksPage() {
  const project = useActiveProject();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [checked, setChecked] = useState<string[]>([]);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    async function tick() {
      try {
        const all = await backend.listJobs(100);
        const list = project ? all.filter((j) => j.projectId === project.id) : all;
        setJobs(list);
        const running = list.find((j) => j.status === "running" || j.status === "queued");
        if (!selected && running) setSelected(running.id);
      } catch (e) {
        setError(toErrorText(e));
      }
    }
    void tick();
    timer.current = setInterval(() => void tick(), 3000);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, project?.id]);

  useEffect(() => {
    if (!selected) return;
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


  const isActive = (job: Job) => job.status === "running" || job.status === "queued";
  const deletable = jobs.filter((j) => !isActive(j));

  async function removeJobs(ids: string[]) {
    if (ids.length === 0) return;
    const notAllowed = ids.filter((id) => jobs.find((j) => j.id === id && isActive(j)));
    if (notAllowed.length > 0) {
      setError("运行中的任务不能删除，请先取消");
      return;
    }
    try {
      const res = await backend.deleteJobs(ids);
      setError(null);
      setChecked((prev) => prev.filter((id) => !ids.includes(id)));
      if (selected && ids.includes(selected)) setSelected(null);
      void backend.listJobs(100).then(setJobs).catch(() => undefined);
      if (res.refused.length > 0) {
        setError(`有 ${res.refused.length} 个运行中的任务未删除`);
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
                    {job.status === "succeeded" &&
                      job.type === "inference" &&
                      !hasDeletedOutput(job) && (
                      <>
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
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={(e) => {
                            e.stopPropagation();
                            if (!window.confirm("删除这次推理的音频与报告？不可恢复。")) return;
                            void backend
                              .jobDeleteOutput(job.id)
                              .then(() => {
                                setError(null);
                                void backend.listJobs(100).then(setJobs).catch(() => undefined);
                              })
                              .catch((err) => setError(toErrorText(err)));
                          }}
                        >
                          删除
                        </Button>
                      </>
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
                              void backend.listJobs(100).then(setJobs).catch(() => undefined);
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
