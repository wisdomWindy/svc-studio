import { useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/Button";
import { listen } from "@tauri-apps/api/event";
import { backend, type BandwidthReport } from "@/lib/backend";
import { useActiveProject } from "@/lib/store";
import { formatDuration, statusLabel } from "@/lib/format";
import {
  datasetStateText,
  gateHeadline,
  prepareButtonLabel,
  type DatasetState,
} from "@/lib/datasetState";
import { DatasetDropZone } from "./DatasetDropZone";
import { toErrorText } from "@/lib/errors";

interface QcMetrics {
  duration?: { min: number; p50: number; p95: number; max: number };
  sampleRate?: Record<string, number>;
  channels?: Record<string, number>;
  silenceRatio?: number;
  clippingRatio?: number;
  loudness?: { meanDbfs: number };
}

interface QcReport {
  summary?: { fileCount: number; totalSeconds: number; passed: boolean; blocking: number };
  metrics?: QcMetrics;
  assertions?: { passed: boolean; blocking: Array<{ code: string; message: string; detail?: string }>; warnings: Array<{ code: string; message: string }> };
}

export function DatasetPage() {
  const project = useActiveProject();
  const [qc, setQc] = useState<QcReport | null>(null);
  const [qcProgress, setQcProgress] = useState<string | null>(null);
  const [scan, setScan] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [importNote, setImportNote] = useState<string | null>(null);
  const [separateVocals, setSeparateVocals] = useState(true);
  const [bandwidth, setBandwidth] = useState<BandwidthReport | null>(null);
  // 处理状态：进入页面只做扫描+质检，绝不再自动分离/切片
  const [prepared, setPrepared] = useState<DatasetState | null>(null);
  // 后端互斥锁：有值表示数据集正在跑别的写操作（分离/切片/质检…）
  const [busyOp, setBusyOp] = useState<string | null>(null);

  // 切换选中项目时自动加载它的数据集状态，不需要用户再点一次「结构扫描」
  useEffect(() => {
    if (!project) {
      setScan(null);
      setQc(null);
      setBandwidth(null);
      return;
    }
    const id = project.id;
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | undefined;
    setScan(null);
    setQc(null);
    setError(null);
    setImportNote(null);
    setScanning(true);

    async function loadAll() {
      try {
        const scanned = await backend.datasetScan(id);
        if (cancelled) return;
        setScan(scanned as Record<string, unknown>);
        // 带宽体检只读一次源文件，导入/切项目后立刻给结论，不等分离跑完
        void backend
          .datasetBandwidth(id)
          .then((b) => {
            if (!cancelled) setBandwidth(b);
          })
          .catch(() => undefined);
        // 处理状态：只读，不触发任何分离/切片
        void backend
          .datasetState(id)
          .then((s) => {
            if (!cancelled) setPrepared(s);
          })
          .catch(() => undefined);
      } catch (e) {
        if (!cancelled) {
          setError(toErrorText(e));
          // 扫描失败也要退出「扫描中」状态，否则界面会一直显示扫描中
          setScanning(false);
        }
        return;
      }
      try {
        const report = await runQc(id);
        if (!cancelled) setQc(report as QcReport);
      } catch {
        // 数据集为空时 QC 会报「还没有可质检的数据集」，这属于正常空态，不弹错误
      } finally {
        if (!cancelled) setScanning(false);
      }
    }

    void (async () => {
      // 先看后端有没有正在跑的写操作：有就**不要**自动跑质检
      // （分离/切片正在写文件时，质检会读到写了一半的数据）
      const running = await backend
        .datasetBusy(id)
        .catch(() => ({ busy: false, op: null as string | null }));
      if (cancelled) return;
      if (running.busy) {
        setBusyOp(running.op ?? "其他操作");
        setScanning(false);
        // 轮询到它结束为止，结束后再正常加载 + 质检
        timer = setInterval(() => {
          void backend
            .datasetBusy(id)
            .then((next) => {
              if (cancelled) return;
              if (!next.busy) {
                if (timer) clearInterval(timer);
                timer = undefined;
                setBusyOp(null);
                setScanning(true);
                void loadAll();
              }
            })
            .catch(() => undefined);
        }, 2000);
        return;
      }
      await loadAll();
    })();

    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
    // 只在项目变化时重新加载
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id]);


  // 启动后台 QC 并等它跑完：进度通过 dataset-progress(kind=qc) 回传
  async function runQc(projectId: string): Promise<QcReport> {
    return new Promise<QcReport>((resolve, reject) => {
      let unlisten: (() => void) | undefined;
      let settled = false;
      const finish = (fn: () => void) => {
        if (settled) return;
        settled = true;
        unlisten?.();
        fn();
      };
      void listen<{
        kind?: string;
        stage: string;
        step?: number;
        total?: number;
        message?: string;
        report?: QcReport;
        code?: string;
      }>("dataset-progress", (event) => {
        const p = event.payload;
        if (p.kind !== "qc") return;
        if (p.stage === "qc") {
          if (p.total && p.total > 0) {
            setQcProgress(`质检中 ${p.step ?? 0}/${p.total}`);
          } else if (p.message) {
            setQcProgress(p.message);
          }
        } else if (p.stage === "qc-done" && p.report) {
          setQcProgress(null);
          finish(() => resolve(p.report as QcReport));
        } else if (p.stage === "qc-error") {
          setQcProgress(null);
          finish(() =>
            reject(new Error(`[${p.code ?? "SVC-3001"}] ${p.message ?? "质检失败"}`)),
          );
        }
      }).then((fn) => {
        unlisten = fn;
      });
      void backend.datasetQc(projectId).catch((e: unknown) => {
        setQcProgress(null);
        finish(() => reject(e instanceof Error ? e : new Error(String(e))));
      });
    });
  }

  // 启动后台准备任务并等待 dataset-progress 事件返回结果
  async function waitForPrepare(projectId: string) {
    return new Promise<Record<string, any>>((resolve, reject) => {
      let unlisten: (() => void) | undefined;
      void listen<{ stage: string; message?: string; result?: Record<string, any>; code?: string; cause?: string }>(
        "dataset-progress",
        (event) => {
          const payload = event.payload;
          if (payload.stage === "done") {
            unlisten?.();
            resolve(payload.result ?? {});
          } else if (payload.stage === "error") {
            unlisten?.();
            reject(new Error(`[${payload.code ?? "SVC-3001"}] ${payload.message ?? "数据集处理失败"}${payload.cause ? `：${payload.cause}` : ""}`));
          } else if (payload.message) {
            setImportNote(payload.message);
          }
        },
      ).then((fn) => {
        unlisten = fn;
      });
      void backend.datasetPrepare(projectId, separateVocals).catch((e) => {
        unlisten?.();
        reject(e);
      });
    });
  }

  async function handleImport(paths: string[]) {
    if (!project) return;
    setBusy("import");
    setError(null);
    setImportNote(null);
    try {
      const result = await backend.datasetImport(project.id, paths);
      const parts = [`已导入 ${result.imported} 个文件`];
      if (result.skipped > 0) parts.push(`跳过 ${result.skipped} 个重复`);
      if (result.rejected > 0) parts.push(`忽略 ${result.rejected} 个非音频`);
      setImportNote(parts.join("，"));
      // 导入后自动：分离 → 转码 → 切片 → 统一响度 → 扫描 → QC
      // dataset_prepare 是后台任务（分离要几分钟），结果通过事件回传
      const prepared = await waitForPrepare(project.id);
      const separatedCount = Array.isArray(prepared.separated) ? prepared.separated.length : 0;
      const slicedCount = Array.isArray(prepared.sliced) ? prepared.sliced.length : 0;
      const skippedSources = Array.isArray(prepared.skippedSources)
        ? prepared.skippedSources.length
        : 0;
      const refinedRejected =
        typeof prepared.refinedRejected === "number" ? prepared.refinedRejected : 0;
      const refinedStats = (prepared.refinedStats ?? {}) as Record<string, number | null>;
      const quality: string[] = [];
      // 超长素材会被自动切段再分离（原文件移入 _original/），如实告诉用户
      const splitSources = Array.isArray(prepared.splitSources) ? prepared.splitSources.length : 0;
      if (splitSources > 0) quality.push(`${splitSources} 个超长素材已先切段再分离`);
      if (skippedSources > 0) quality.push(`体检删除 ${skippedSources} 个不可用源文件`);
      if (refinedRejected > 0) quality.push(`精修删除 ${refinedRejected} 个不合格切片`);
      const sepQuality = (prepared.separationQuality ?? {}) as Record<string, number | null>;
      if (typeof sepQuality.medianBleed === "number") {
        quality.push(`分离泄漏中位 ${sepQuality.medianBleed.toFixed(3)}`);
      }
      if (refinedStats.rolloffHzMedian) {
        quality.push(`保留切片带宽中位 ${Math.round(refinedStats.rolloffHzMedian / 100) / 10} kHz`);
      }
      if (separatedCount > 0 || slicedCount > 0 || prepared.keptCount || quality.length) {
        setImportNote(
          [
            `${parts.join("，")}；已分离 ${separatedCount} 个、切片 ${slicedCount} 个`,
            quality.join("、"),
            `最终保留 ${prepared.keptCount ?? 0} 段干声（原始文件与中间产物已清理）`,
          ]
            .filter(Boolean)
            .join("，"),
        );
      }
      const scanned = await backend.datasetScan(project.id);
      setScan(scanned);
      setQc(await runQc(project.id));
      // 导入后立刻给带宽预估（只读源文件），不用等分离跑完
      void backend.datasetBandwidth(project.id).then(setBandwidth).catch(() => undefined);
      // 处理链刚跑过：刷新「已处理」状态，按钮随之变成「重新处理（仅新文件）」
      void backend.datasetState(project.id).then(setPrepared).catch(() => undefined);
    } catch (e) {
      setError(toErrorText(e));
    } finally {
      setBusy(null);
    }
  }

  /** 重新跑一遍数据集处理链：分离 → 转码 → 切片 → 44.1k → 精修 → 清理 */
  async function startPrepare() {
    if (!project) return;
    setBusy("prepare");
    setError(null);
    setImportNote(null);
    try {
      void backend.datasetPrepare(project.id, separateVocals).catch((e) => {
        setError(toErrorText(e));
      });
      const prepared = await waitForPrepare(project.id);
      const separated = Array.isArray(prepared.separated) ? prepared.separated.length : 0;
      const sliced = Array.isArray(prepared.sliced) ? prepared.sliced.length : 0;
      setImportNote(
        `处理完成：已分离 ${separated} 个、切片 ${sliced} 个，最终保留 ${prepared.keptCount ?? 0} 段；` +
          "下一步去「预处理」提取特征",
      );
      setScan(await backend.datasetScan(project.id));
      setQc(await runQc(project.id));
      void backend.datasetBandwidth(project.id).then(setBandwidth).catch(() => undefined);
      void backend.datasetState(project.id).then(setPrepared).catch(() => undefined);
    } catch (e) {
      setError(toErrorText(e));
    } finally {
      setBusy(null);
    }
  }

  async function run<T>(label: string, fn: () => Promise<T>, apply: (value: T) => void) {
    if (!project) return;
    setBusy(label);
    setError(null);
    try {
      apply(await fn());
    } catch (e) {
      setError(toErrorText(e));
    } finally {
      setBusy(null);
    }
  }

  if (!project) {
    return (
      <>
        <PageHeader title="数据集" description="先创建一个项目，再把素材导入它的 dataset_raw 目录。" />
        <div className="rounded-lg border border-dashed border-line p-10 text-center text-[12px] text-muted">
          还没有项目
        </div>
      </>
    );
  }

  const metrics = qc?.metrics;
  const total = qc?.summary?.totalSeconds ?? 0;

  return (
    <>
      <PageHeader
        title="数据集"
        description="结构校验 → QC 七项 → 切片 → 统一 44.1k 单声道。阻断项清零才能进入预处理。"
        actions={
          <div className="flex gap-2">
            <Button disabled={!!busy || !!busyOp} onClick={() => void run("scan", () => backend.datasetScan(project.id), setScan)}>
              {busy === "scan" ? "扫描中…" : "结构扫描"}
            </Button>
            <Button variant="primary" disabled={!!busy || !!busyOp} onClick={() => void run("qc", () => runQc(project.id), setQc)}>
              {busy === "qc" ? "质检中…" : "运行 QC"}
            </Button>
          </div>
        }
        badge={<span className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted">{project.name}</span>}
      />

      {error && <ErrorNotice error={error} compact />}

      <DatasetDropZone
        disabled={!!busy || !!busyOp}
        busy={busy === "import"}
        onImport={(paths) => void handleImport(paths)}
      />

      <label className="mt-2 flex items-center gap-2 text-[12px] text-muted">
        <input
          type="checkbox"
          checked={separateVocals}
          onChange={(e) => setSeparateVocals(e.target.checked)}
        />
        自动分离人声（去伴奏 / 背景音，UVR5 MDX23C；失败自动回退 Demucs；约 1 分钟音频 15 秒）
      </label>

      {qcProgress && (
        <p className="mt-2 flex items-center gap-2 text-[12px] text-muted">
          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
          {qcProgress}
        </p>
      )}

      {scanning && (
        <p className="mt-2 flex items-center gap-2 text-[12px] text-muted">
          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
          正在扫描数据集并计算 QC 指标（文件多时需要一点时间）…
        </p>
      )}

      {importNote && (
        <p className="mt-2 text-[12px] text-ok">
          {importNote}
          <span className="ml-1 text-muted">（已刷新结构扫描与 QC）</span>
        </p>
      )}

      {/* 互斥提示：别的写操作在跑时，这里的所有按钮都禁用 */}
      {busyOp && (
        <div className="mt-2 rounded-lg border border-warn/40 bg-warn/5 px-3 py-2 text-[12px] text-warn">
          数据集正在执行「{busyOp}」——期间不允许质检、切片、分离等操作（避免读到写了一半的文件）。
          结束后会自动刷新并跑质检。
        </div>
      )}

      {/* 处理状态：明确告诉用户「进入本页只做质检，不会重跑分离/切片」 */}
      <p className="mt-2 text-[11px] text-muted" data-testid="dataset-state">
        {datasetStateText(prepared)}
      </p>

      <div className="grid gap-3 lg:grid-cols-[1.55fr_1fr]">
        {/* 左列：QC 报告 */}
        <div>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {[
              ["总时长", scanning ? "扫描中…" : total ? formatDuration(total) : "—"],
              ["文件数", scanning ? "扫描中…" : (qc?.summary?.fileCount ?? "—")],
              ["静音比", scanning ? "…" : metrics?.silenceRatio != null ? `${(metrics.silenceRatio * 100).toFixed(1)}%` : "—"],
              ["削波比", scanning ? "…" : metrics?.clippingRatio != null ? `${(metrics.clippingRatio * 100).toFixed(3)}%` : "—"],
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg border border-line bg-surface p-3">
                <div className="text-[11px] text-muted">{label}</div>
                <div className="mt-1 text-[18px] font-semibold tabular">{value}</div>
              </div>
            ))}
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              variant={prepared?.upToDate ? "ghost" : "primary"}
              disabled={!!busy || !!busyOp}
              onClick={() => void startPrepare()}
              title={
                prepared?.upToDate
                  ? "已处理过：重跑只会处理新增文件（已分离/已切片的会跳过）"
                  : "分离 → 转码 → 切片 → 统一 44.1k → 精修 → 清理；预处理在下一步单独跑"
              }
            >
              {prepareButtonLabel(prepared, busy === "prepare")}
            </Button>
            <Button disabled={!!busy || !!busyOp} onClick={() => void run("slice", () => backend.datasetSlice(project.id), () => setScan(null))}>
              {busy === "slice" ? "切片中…" : "切片 5–15s"}
            </Button>
            <Button disabled={!!busy || !!busyOp} onClick={() => void run("resample", () => backend.datasetResample(project.id), () => setScan(null))}>
              {busy === "resample" ? "转换中…" : "统一 44.1k 单声道"}
            </Button>
          </div>

          {qc?.assertions && (
            <div className="mt-3 rounded-lg border border-line bg-surface p-3.5">
              <div className="flex items-center justify-between">
                <span className="text-[13px] font-medium">门禁结论</span>
                <span className={qc.assertions.passed ? "text-[12px] text-ok" : "text-[12px] text-danger"}>
                  {gateHeadline(qc.assertions)}
                </span>
              </div>
              <ul className="mt-2 space-y-1 text-[12px]">
                {qc.assertions.blocking.map((item) => (
                  <li key={item.code + item.message} className="text-danger">
                    [{item.code}] {item.message}
                    {item.detail ? `（${item.detail}）` : ""}
                  </li>
                ))}
                {qc.assertions.warnings.map((item) => (
                  <li key={item.code + item.message} className="text-warn">
                    [{item.code}] {item.message}
                  </li>
                ))}
                {qc.assertions.blocking.length === 0 && qc.assertions.warnings.length === 0 && (
                  <li className="text-muted">没有发现问题</li>
                )}
              </ul>
            </div>
          )}
        </div>

        {/* 右列：素材带宽体检（Layer 1） */}
        <div>
          {bandwidth && bandwidth.files > 0 ? (
            <div
              className={`rounded-lg border p-3.5 ${
                bandwidth.verdict.level === "good"
                  ? "border-ok/40 bg-ok/5"
                  : bandwidth.verdict.level === "ok"
                    ? "border-line bg-surface"
                    : "border-warn/40 bg-warn/5"
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2 text-[12px]">
                <span className="font-medium">素材带宽体检</span>
                <span className="text-[11px] text-muted">{bandwidth.files} 个文件</span>
              </div>
              <div className="mt-2 flex items-baseline justify-between">
                <span className="text-[11px] text-muted">中位频谱 rolloff</span>
                <span className="tabular text-[18px] font-semibold">
                  {bandwidth.medianRolloffHz
                    ? `${(bandwidth.medianRolloffHz / 1000).toFixed(1)} kHz`
                    : "—"}
                </span>
              </div>
              <div className="mt-1 flex items-baseline justify-between">
                <span className="text-[11px] text-muted">预估输出亮度上限</span>
                <span className="tabular text-[18px] font-semibold">
                  {bandwidth.estimatedOutputCeilingHz
                    ? `${(bandwidth.estimatedOutputCeilingHz / 1000).toFixed(1)} kHz`
                    : "—"}
                </span>
              </div>
              <div className="mt-2 flex items-center justify-between text-[11px]">
                <span className="text-muted">0</span>
                <span className={bandwidth.verdict.level === "good" ? "text-ok" : "text-warn"}>
                  {bandwidth.verdict.label}
                </span>
                <span className="text-muted">
                  奈奎斯特{" "}
                  {bandwidth.nyquistHz && bandwidth.nyquistHz.length > 0
                    ? `${(Math.max(...bandwidth.nyquistHz) / 1000).toFixed(2)} kHz`
                    : "—"}
                </span>
              </div>
              <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-raised">
                <div
                  className={`h-full rounded-full ${bandwidth.verdict.level === "good" ? "bg-ok" : "bg-warn"}`}
                  style={{
                    width: `${Math.min(
                      100,
                      ((bandwidth.estimatedOutputCeilingHz ?? 0) / 22050) * 100,
                    )}%`,
                  }}
                />
              </div>
              <p className="mt-2 text-[11px] leading-relaxed text-muted">{bandwidth.verdict.advice}</p>
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
                {Object.entries(bandwidth.buckets)
                  .sort((a, b) => b[1] - a[1])
                  .map(([label, count]) => (
                    <span key={label} className="tabular">
                      {label}: {count}
                    </span>
                  ))}
              </div>
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-line bg-surface/50 p-4 text-[12px] text-muted">
              导入音频后自动检测素材带宽：它决定模型输出的亮度上限。
            </div>
          )}
        </div>
      </div>

      {scan && (
        <div className="mt-3 rounded-lg border border-line bg-surface p-3.5 text-[12px]">
          <div className="font-medium">扫描结果</div>
          <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-[11px] text-muted">
            {JSON.stringify(scan, null, 2)}
          </pre>
        </div>
      )}

      {qc && !qc.assertions?.passed && (
        <p className="mt-3 text-[11px] leading-relaxed text-muted">
          阻断项不清零，预处理按钮会保持禁用。当前状态：{statusLabel(qc.assertions?.passed ? "succeeded" : "failed")}。
        </p>
      )}
    </>
  );
}
