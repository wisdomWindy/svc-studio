import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/Button";
import { backend, type CheckpointInfo, type Recommend } from "@/lib/backend";
import { useActiveProject } from "@/lib/store";
import { formatBytes, formatDuration, statusLabel } from "@/lib/format";
import { LOSS_STROKE, parseLossSeries, parseTrainingTelemetry, sparklinePath } from "@/lib/curve";
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
  // 用「步数」判断是否已登记：模型登记后文件会被复制到 data/models，
  // 路径与 logs/44k 下的 checkpoint 不再一致，按路径比对会永远显示「登记为模型」。
  const [registeredSteps, setRegisteredSteps] = useState<Set<number>>(new Set());

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
        const status = job?.status;
    setRunning(status === "running" || status === "queued" || status === "stopping");
    // 持久化的「停止中」：切页或刷新后按钮仍是【停止中…】
    setStopping(status === "stopping");
    if (status === "cancelled" || status === "failed" || status === "succeeded") {
      setActiveJobId(null);
    }
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
      setRegisteredSteps(
        new Set(
          models
            .filter((m) => !project || !m.projectId || m.projectId === project.id)
            .map((m) => Number(m.step))
            .filter((n) => Number.isFinite(n)),
        ),
      );
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
        "清空当前训练产物并重新训练？\n\n将删除 logs/44k 与 logs/diffusion：\n· 所有 checkpoint\n· 浅扩散模型\n· 聚类模型 kmeans_10000.pt\n· 特征索引 feature_and_index.pkl\n\n已登记到模型库的模型不受影响。",
      )
    )
      return;
    setArchiveBusy(true);
    setError(null);
    try {
      await backend.trainReset(project.id);
      setCheckpoints([]);
      setMessage(`已清空训练产物（checkpoint / 扩散模型 / 聚类 / 索引），可以重新训练了`);
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

  async function start(smoke: boolean, resume = false, kind: "main" | "diffusion" = "main") {
    if (!project) return;
    setBusy(smoke ? "smoke" : kind === "diffusion" ? "diffusion" : "train");
    setError(null);
    setMessage(null);
    try {
      const id = await backend.trainStart(project.id, smoke, resume, kind);
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
                  onClick={() => void start(false, false, "diffusion")}
                >
                  {busy === "diffusion" ? "启动中…" : "训练扩散模型"}
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
                    {archiveBusy ? "清理中…" : "重新训练"}
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

      {(() => {
        const telemetry = parseTrainingTelemetry(events, progress.total);
        if (progress.step == null && telemetry.lastStep == null) return null;
        const percent =
          progress.step != null && progress.total
            ? Math.min(100, Math.round((progress.step / progress.total) * 100))
            : null;
        const vramGb =
          telemetry.vramPeakMb != null
            ? `${(telemetry.vramPeakMb / 1024).toFixed(2)} GB`
            : "—";
        return (
          <div className="mb-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <div className="rounded-lg border border-line bg-surface p-3">
              <div className="text-[11px] text-muted">当前步数</div>
              <div className="mt-1 text-[18px] font-semibold tabular">
                {progress.step ?? telemetry.lastStep ?? 0}
                {progress.total ? <span className="text-[12px] text-muted"> / {progress.total}</span> : null}
              </div>
              {percent != null && (
                <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-raised">
                  <div className="h-full rounded-full bg-accent" style={{ width: `${percent}%` }} />
                </div>
              )}
            </div>
            <div className="rounded-lg border border-line bg-surface p-3">
              <div className="text-[11px] text-muted">预计剩余</div>
              <div className="mt-1 text-[18px] font-semibold tabular">
                {telemetry.etaSec != null ? formatDuration(telemetry.etaSec) : "—"}
              </div>
              <div className="mt-1 text-[11px] text-muted">
                {telemetry.secPerStep != null ? `${telemetry.secPerStep.toFixed(2)} s/步` : "采集中…"}
              </div>
            </div>
            <div className="rounded-lg border border-line bg-surface p-3">
              <div className="text-[11px] text-muted">显存峰值</div>
              <div className="mt-1 text-[18px] font-semibold tabular">{vramGb}</div>
              <div className="mt-1 text-[11px] text-muted">
                {telemetry.vramMb != null ? `当前 ${(telemetry.vramMb / 1024).toFixed(2)} GB` : "未采样"}
              </div>
            </div>
            <div className="rounded-lg border border-line bg-surface p-3">
              <div className="text-[11px] text-muted">参数推荐</div>
              <div className="mt-1 text-[18px] font-semibold tabular">
                {recommend ? `${recommend.batchSize} batch` : "—"}
              </div>
              <div className="mt-1 text-[11px] text-muted">
                {recommend ? `duration ${recommend.diffusionDuration}s · 进程 ${recommend.numProcesses}` : "读取中…"}
              </div>
            </div>
          </div>
        );
      })()}

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
                    (registeredSteps.has(Number(checkpoint.step)) ? (
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
              <path d={path} fill="none" stroke={LOSS_STROKE} strokeWidth="1.5" />
            </svg>
          </div>
        );
      })()}

      {(() => {
        // 事件时间线：降档 / 重启 / 发散 / 当前步 —— 这些 worker 一直在发，之前埋在任务日志里
        const KEY = /降档|降到|重试|重启|继续|发散|显存不足|NaN|OOM/;
        const timeline = events
          .filter((e) => e.code !== "CURVE" && (e.level !== "info" || KEY.test(e.message ?? "")))
          .slice(-6);
        if (timeline.length === 0 && progress.step == null) return null;
        const clock = (ts: string) => {
          const d = new Date(ts);
          return Number.isNaN(d.getTime()) ? "" : d.toTimeString().slice(0, 8);
        };
        return (
          <div className="mt-3 rounded-lg border border-line bg-surface p-3">
            <div className="flex items-center justify-between text-[12px]">
              <span className="font-medium">事件时间线</span>
              <span className="text-[11px] text-muted">降档 / 重启 / 当前</span>
            </div>
            <ul className="mt-2 space-y-1.5 text-[12px]">
              {timeline.map((e) => (
                <li key={`${e.seq}-${e.code ?? ""}`} className="flex items-start gap-2">
                  <span className="tabular shrink-0 text-[11px] text-muted">{clock(e.ts)}</span>
                  <span
                    className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] ${
                      e.level === "error"
                        ? "bg-danger/15 text-danger"
                        : e.level === "warn"
                          ? "bg-warn/15 text-warn"
                          : "bg-raised text-muted"
                    }`}
                  >
                    {KEY.test(e.message ?? "") ? "调整" : e.level === "error" ? "错误" : "提示"}
                  </span>
                  <span className="min-w-0 break-words text-muted">{e.message}</span>
                </li>
              ))}
              {progress.step != null && (
                <li className="flex items-start gap-2">
                  <span className="shrink-0 rounded bg-accent/15 px-1.5 py-0.5 text-[10px] text-accent">
                    当前
                  </span>
                  <span className="tabular text-muted">
                    第 {progress.step} 步{progress.total ? ` / ${progress.total}` : ""}
                    {running ? " · 运行中" : " · 已停止"}
                  </span>
                </li>
              )}
            </ul>
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
