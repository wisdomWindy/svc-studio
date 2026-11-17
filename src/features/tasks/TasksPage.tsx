import { useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/Button";
import { backend } from "@/lib/backend";
import { formatClock, percent, statusLabel, statusTone } from "@/lib/format";
import type { Job, JobEvent } from "@/types";

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

export function TasksPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    async function tick() {
      try {
        const list = await backend.listJobs(100);
        setJobs(list);
        const running = list.find((j) => j.status === "running" || j.status === "queued");
        if (!selected && running) setSelected(running.id);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    }
    void tick();
    timer.current = setInterval(() => void tick(), 3000);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

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

  async function cancel(job: Job) {
    try {
      await backend.cancelJob(job.id);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <>
      <PageHeader
        title="任务"
        description="所有作业的统一视图：谁在跑、跑到哪、为什么失败。状态由后端状态机维护，前端只呈现。"
        badge={
          <span className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted">
            {jobs.length} 条
          </span>
        }
      />

      {error && <ErrorNotice error={error} compact />}

      <div className="grid gap-3 lg:grid-cols-[1.2fr_1fr]">
        <div className="overflow-hidden rounded-lg border border-line">
          <table className="w-full text-left text-[12px]">
            <thead className="bg-raised text-muted">
              <tr>
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
                  <td className="px-3 py-1.5">{job.type}</td>
                  <td className={`px-3 py-1.5 ${TONE[statusTone(job.status)]}`}>
                    {statusLabel(job.status)}
                    {job.errorCode ? ` · ${job.errorCode}` : ""}
                  </td>
                  <td className="px-3 py-1.5 tabular">{percent(job.progress)}</td>
                  <td className="px-3 py-1.5 tabular text-muted">{formatClock(job.startedAt)}</td>
                  <td className="px-3 py-1.5 text-right">
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
                            .then(() => setError(null))
                            .catch((err) =>
                              setError(err instanceof Error ? err.message : String(err)),
                            );
                        }}
                      >
                        重试
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="rounded-lg border border-line bg-surface">
          <div className="border-b border-line px-3 py-2 text-[12px] text-muted">
            {selected ? `事件 · ${selected}` : "选择左侧任务查看日志"}
          </div>
          <div className="max-h-[420px] overflow-y-auto px-3 py-2 font-mono text-[11px] leading-relaxed">
            {events.length === 0 && <p className="text-muted">暂无事件</p>}
            {events.map((event) => (
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
