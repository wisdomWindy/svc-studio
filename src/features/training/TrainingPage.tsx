import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/Button";
import { backend, type CheckpointInfo, type Recommend } from "@/lib/backend";
import { useActiveProject } from "@/lib/store";
import { formatBytes, statusLabel } from "@/lib/format";
import { parseLossSeries, sparklinePath } from "@/lib/curve";
import type { JobEvent } from "@/types";
import { toErrorText } from "@/lib/errors";

export function TrainingPage() {
  const project = useActiveProject();
  const [recommend, setRecommend] = useState<Recommend | null>(null);
  const [checkpoints, setCheckpoints] = useState<CheckpointInfo[]>([]);
  const [archiveBusy, setArchiveBusy] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [gate, setGate] = useState<{ trainable: boolean; blocking: string[] } | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [gpuBusy, setGpuBusy] = useState(false);
  const [running, setRunning] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  // 已发出停止请求、但进程还没退出：这期间继续显示「停止中…」，避免按钮闪回
  const [stopping, setStopping] = useState(false);
  const [auxBusy, setAuxBusy] = useState<null | "cluster" | "index">(null);
  const [progress, setProgress] = useState<{ step: number | null; total: number | null }>({
    step: null,
    total: null,
  });
  const [registeredPaths, setRegisteredPaths] = useState<Set<string>>(new Set());

  useEffect(() => {
    const tick = () => void backend.gpuBusy().then(setGpuBusy).catch(() => setGpuBusy(false));
    tick();
    const id = setInterval(tick, 3000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    void backend
      .doctorCached()
      .then(setGate)
      .catch(() => setGate(null));
  }, []);

  useEffect(() => {
    void backend
      .trainRecommend()
      .then(setRecommend)
      .catch(() => setRecommend(null));
  }, []);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;

    const tick = async () => {
      try {
        const [page, job] = await Promise.all([
          backend.jobEvents(jobId, 0, 2000),
          backend.getJob(jobId),
        ]);
        if (cancelled) return;
        setEvents(page.events);
        // 只有 running/queued 才算"正在训练"，完成后按钮要变回来
        setRunning(job?.status === "running" || job?.status === "queued");
        setProgress({ step: job?.step ?? null, total: job?.total ?? null });
      } catch {
        // 轮询失败不打断界面
      }
    };

    void tick();
    const id = setInterval(() => void tick(), 3000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [jobId]);

  const refreshRegistered = useCallback(async () => {
    try {
      const models = await backend.listModels();
      const norm = (p: string) => p.replace(/\\/g, "/").toLowerCase();
      setRegisteredPaths(new Set(models.map((m) => norm(m.path))));
    } catch {
      /* 读取失败时保持空集合，不影响训练 */
    }
  }, []);

  useEffect(() => {
    void refreshRegistered();
  }, [refreshRegistered, jobId]);

  async function archiveAndReset() {
    if (!project) return;
    if (
      !window.confirm(
        "归档当前训练产物并重新开始？\n\n旧的 checkpoint 会改名保留在 logs/44k_<时间戳>，不会被删除。",
      )
    )
      return;
    setArchiveBusy(true);
    setError(null);
    try {
      const archived = await backend.trainArchive(project.id);
      setCheckpoints([]);
      setMessage(`已归档到 ${archived.split(/[\\/]/).pop()}，可以重新开始训练了`);
    } catch (e) {
      setError(toErrorText(e));
    } finally {
      setArchiveBusy(false);
    }
  }

  const refreshCheckpoints = useCallback(async () => {
    if (!project) return;
    try {
      const list = await backend.trainCheckpoints(project.id);
      setCheckpoints(list);
    } catch {
      /* 扫描失败不打断界面 */
    }
  }, [project]);

  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => void refreshCheckpoints(), 3000);
    return () => clearInterval(id);
  }, [running, refreshCheckpoints]);

  useEffect(() => {
    setCheckpoints([]);
    setProgress({ step: null, total: null });
    if (!project) return;
    let cancelled = false;
    void backend
      .trainCheckpoints(project.id)
      .then((list) => {
        if (!cancelled) setCheckpoints(list);
      })
      .catch(() => {
        if (!cancelled) setCheckpoints([]);
      });
    return () => {
      cancelled = true;
    };
  }, [project?.id, jobId]);


  // 训练是否在跑要以任务表为准：页面刷新、从任务页启动都要能对上
  useEffect(() => {
    if (!project) return;
    let cancelled = false;
    async function poll() {
      try {
        const jobs = await backend.listJobs(50);
        if (cancelled) return;
        const active = jobs.find(
          (j) =>
            j.type.startsWith("train_") &&
            (j.status === "running" || j.status === "queued") &&
            (!j.projectId || j.projectId === project!.id),
        );
        setRunning(Boolean(active));
        if (!active) setStopping(false);
        setActiveJobId(active?.id ?? null);
        if (active) setJobId(active.id);
      } catch {
        /* 拿不到列表就保持现状 */
      }
    }
    void poll();
    const timer = window.setInterval(poll, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [project?.id]);

  async function start(smoke: boolean, resume = false) {
    if (!project) return;
    setBusy(smoke ? "smoke" : "train");
    setError(null);
    setMessage(null);
    try {
      const id = await backend.trainStart(project.id, smoke, resume);
      setJobId(id);
      setRunning(true);
      setMessage(`${smoke ? "冒烟" : "训练"}已启动：${id}`);
    } catch (e) {
      setError(toErrorText(e));
    } finally {
      setBusy(null);
    }
  }

  async function stop() {
    // 目标要实时确认：activeJobId/jobId 可能还没被轮询填上，静默 return 会表现为「点了没反应」
    let target = activeJobId ?? jobId;
    if (!target) {
      try {
        const jobs = await backend.listJobs(50);
        const active = jobs.find(
          (j) =>
            j.type.startsWith("train_") &&
            (j.status === "running" || j.status === "queued") &&
            (!project || !j.projectId || j.projectId === project.id),
        );
        target = active?.id ?? null;
      } catch {
        /* 忽略，下面统一提示 */
      }
    }
    if (!target) {
      setRunning(false);
      setStopping(false);
      setMessage("当前没有正在运行的训练任务");
      return;
    }
    setStopping(true);
    try {
      await backend.trainStop(target);
      setMessage("已请求停止，正在等待进程退出（进度保留到最近一次 checkpoint）");
    } catch (e) {
      setStopping(false);
      setError(toErrorText(e));
    }
  }


  // 可选任务：聚类（kmeans）/ 特征检索索引（index），推理页的聚类与特征检索依赖它们
  async function runAux(kind: "cluster" | "index") {
    if (!project) return;
    setAuxBusy(kind);
    setError(null);
    setMessage(null);
    try {
      const result = await backend.trainAux(project.id, kind);
      const label = kind === "cluster" ? "聚类模型" : "特征检索索引";
      setMessage(`${label}已生成：${String((result as Record<string, unknown>).path ?? "")}`);
    } catch (e) {
      setError(toErrorText(e));
    } finally {
      setAuxBusy(null);
    }
  }

  async function register(checkpoint: CheckpointInfo) {
    if (!project) return;
    try {
      const result = await backend.registerModel(project.id, checkpoint.path);
      setMessage(
        result.alreadyRegistered
          ? `该 checkpoint 已登记过：${result.model.name}`
          : `已登记 ${result.model.name}${result.becameDefault ? "（已设为默认）" : ""}`,
      );
      await refreshRegistered();
    } catch (e) {
      setError(toErrorText(e));
    }
  }

  if (!project) {
    return (
      <>
        <PageHeader title="训练" description="先创建项目并完成预处理。" />
        <div className="rounded-lg border border-dashed border-line p-10 text-center text-[12px] text-muted">
          还没有项目
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="训练"
        description="先跑 100 步冒烟（可落盘、可续训），再进长训。停止就是停止，续训从最近 checkpoint 继续。"
        actions={
          <div className="flex gap-2">
            {running ? (
              <Button variant="primary" disabled={!!busy || stopping} onClick={() => void stop()}>
                {stopping ? "停止中…" : "停止训练"}
              </Button>
            ) : (
              <>
                <Button
                  disabled={!!busy || running || gate?.trainable === false}
                  onClick={() => void start(true)}
                >
                  {busy === "smoke" ? "启动中…" : "100 步冒烟"}
                </Button>
                <Button
                  variant="primary"
                  disabled={!!busy}
                  onClick={() => void start(false, checkpoints.length > 0)}
                >
                  {busy === "train"
                    ? "启动中…"
                    : checkpoints.length > 0
                      ? "继续训练"
                      : "开始训练"}
                </Button>
                {checkpoints.length > 0 && (
                  <Button disabled={!!busy || archiveBusy} onClick={() => void archiveAndReset()}>
                    {archiveBusy ? "归档中…" : "归档并重新训练"}
                  </Button>
                )}
              </>
            )}
          </div>
        }
        badge={
          <div className="flex items-center gap-2">
            <span
              className={`rounded-full border px-2 py-0.5 text-[11px] ${
                gpuBusy ? "border-warn/50 text-warn" : "border-line text-muted"
              }`}
            >
              GPU {gpuBusy ? "忙" : "闲"}
            </span>
            <span className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted">{project.name}</span>
          </div>
        }
      />

      {gate?.trainable === false && (
        <div className="mb-2 rounded-lg border border-warn/40 bg-warn/5 px-3 py-2 text-[12px] text-warn">
          环境自检未通过，训练与预处理入口已禁用。阻断项：{gate.blocking.join("、") || "未知"}；请在设置页修复后重新自检。
        </div>
      )}
      {message && <p className="mb-2 text-[12px] text-ok">{message}</p>}
      {error && <ErrorNotice error={error} compact />}

      <div className="grid gap-3 lg:grid-cols-[1fr_1.2fr]">
        <div className="space-y-3">
          <div className="rounded-lg border border-line bg-surface p-3.5">
            <div className="text-[13px] font-medium">显存推荐</div>
            {recommend ? (
              <>
                <div className="mt-1 text-[13px]">
                  batch <span className="font-semibold tabular">{recommend.batchSize}</span> · duration{" "}
                  <span className="font-semibold tabular">{recommend.diffusionDuration}s</span> · 进程{" "}
                  <span className="tabular">{recommend.numProcesses}</span>
                </div>
                <p className="mt-1 text-[11px] leading-relaxed text-muted">{recommend.reason}</p>
                {!recommend.trainable && <p className="mt-1 text-[12px] text-danger">当前显存不可训练</p>}
              </>
            ) : (
              <p className="mt-1 text-[12px] text-muted">读取中…</p>
            )}
          </div>
          <div className="rounded-lg border border-line bg-surface p-3.5 text-[12px] text-muted">
            <div className="text-[13px] font-medium text-ink">停止与续训</div>
            <p className="mt-1 leading-relaxed">
              训练没有“暂停”，停止后最多回退到最近一次保存（默认每 800 步）。已有 checkpoint 时按钮变为「继续训练」，会从最新 checkpoint 接着跑；要从零开始需先归档 <code>logs/44k</code>。
            </p>
          </div>

          <div className="rounded-lg border border-line bg-surface p-3.5">
            <div className="text-[13px] font-medium text-ink">可选任务</div>
            <p className="mt-1 text-[11px] leading-relaxed text-muted">
              推理页的「聚类比例」与「特征检索」依赖下面两个产物，训练完成后才能生效。
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              <Button size="sm" disabled={!!auxBusy || checkpoints.length === 0} onClick={() => void runAux("cluster")}>
                {auxBusy === "cluster" ? "训练中…" : "训练聚类模型"}
              </Button>
              <Button size="sm" disabled={!!auxBusy || checkpoints.length === 0} onClick={() => void runAux("index")}>
                {auxBusy === "index" ? "训练中…" : "训练特征检索索引"}
              </Button>
            </div>
            <p className="mt-2 text-[11px] text-muted">
              聚类产出 kmeans_10000.pt，索引产出 feature_and_index.pkl；各需几分钟。
            </p>
          </div>
        </div>

        <div className="overflow-hidden rounded-lg border border-line">
          <div className="border-b border-line bg-raised px-3 py-2 text-[12px] text-muted">checkpoint</div>
          <table className="w-full text-left text-[12px]">
            <tbody>
              {checkpoints.length === 0 && (
                <tr>
                  <td className="px-3 py-8 text-center text-muted">还没有 checkpoint</td>
                </tr>
              )}
              {checkpoints.map((checkpoint) => (
                <tr key={checkpoint.path} className="border-t border-line/60">
                  <td className="px-3 py-1.5 tabular">
                      {checkpoint.step} 步
                      {checkpoint.smoke && (
                        <span className="ml-1 rounded bg-raised px-1 py-0.5 text-[10px] text-muted">冒烟</span>
                      )}
                    </td>
                  <td className="px-3 py-1.5 tabular text-muted">{formatBytes(checkpoint.bytes)}</td>
                  <td className="px-3 py-1.5 text-muted">{checkpoint.modified}</td>
                  <td className="px-3 py-1.5 text-right">
                    {!checkpoint.smoke &&
                    (registeredPaths.has(checkpoint.path.replace(/\\/g, "/").toLowerCase()) ? (
                      <span className="rounded border border-line px-2 py-0.5 text-[11px] text-muted">
                        已登记
                      </span>
                    ) : (
                      <Button size="sm" onClick={() => void register(checkpoint)}>
                        登记为模型
                      </Button>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {(() => {
        const points = parseLossSeries(events);
        const path = sparklinePath(points, 520, 96);
        if (!path) return null;
        const last = points[points.length - 1];
        return (
          <div className="mt-3 rounded-lg border border-line bg-surface p-3">
            <div className="flex items-center justify-between text-[12px]">
              <span className="font-medium">loss 曲线</span>
              <span className="tabular text-muted">
                第 {last?.step} 步 · loss {last?.value.toFixed(4)}
              </span>
            </div>
            <svg viewBox="0 0 520 96" className="mt-1 h-24 w-full" role="img" aria-label="训练 loss 曲线">
              <path d={path} fill="none" stroke="var(--accent, #d66a3a)" strokeWidth="1.5" />
            </svg>
          </div>
        );
      })()}

      <p className="mt-3 text-[11px] text-muted">
        当前状态：{running ? "运行中" : statusLabel(jobId ? "succeeded" : "queued")}
          {progress.step != null && (
            <span className="ml-2 text-muted">下一个保存点 <span className="tabular">{Math.ceil(((progress.step ?? 0) + 1) / 200) * 200}</span> 步</span>
          )}
          {progress.step != null && (
            <span className="ml-2 text-muted">当前步数 <span className="tabular text-ink">{progress.step}</span>{progress.total ? ` / ${progress.total}` : ""}</span>
          )}
        {jobId ? ` · 任务 ${jobId}` : ""} · 冒烟产物在 logs/_smoke，不会污染正式模型目录。
      </p>
    </>
  );
}
