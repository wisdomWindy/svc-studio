import { useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/Button";
import { listen } from "@tauri-apps/api/event";
import { backend } from "@/lib/backend";
import { useActiveProject } from "@/lib/store";
import { formatDuration, statusLabel } from "@/lib/format";
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

  // 切换选中项目时自动加载它的数据集状态，不需要用户再点一次「结构扫描」
  useEffect(() => {
    if (!project) {
      setScan(null);
      setQc(null);
      return;
    }
    const id = project.id;
    let cancelled = false;
    setScan(null);
    setQc(null);
    setError(null);
    setImportNote(null);
    setScanning(true);

    void (async () => {
      try {
        const scanned = await backend.datasetScan(id);
        if (cancelled) return;
        setScan(scanned as Record<string, unknown>);
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
    })();

    return () => {
      cancelled = true;
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
      if (separatedCount > 0 || slicedCount > 0 || prepared.keptCount) {
        setImportNote(
          `${parts.join("，")}；已分离 ${separatedCount} 个、切片 ${slicedCount} 个，最终保留 ${prepared.keptCount ?? 0} 段干声（原始文件与中间产物已清理）`,
        );
      }
      const scanned = await backend.datasetScan(project.id);
      setScan(scanned);
      setQc(await runQc(project.id));
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
            <Button disabled={!!busy} onClick={() => void run("scan", () => backend.datasetScan(project.id), setScan)}>
              {busy === "scan" ? "扫描中…" : "结构扫描"}
            </Button>
            <Button variant="primary" disabled={!!busy} onClick={() => void run("qc", () => runQc(project.id), setQc)}>
              {busy === "qc" ? "质检中…" : "运行 QC"}
            </Button>
          </div>
        }
        badge={<span className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted">{project.name}</span>}
      />

      {error && <ErrorNotice error={error} compact />}

      <DatasetDropZone
        disabled={!!busy}
        busy={busy === "import"}
        onImport={(paths) => void handleImport(paths)}
      />

      <label className="mt-2 flex items-center gap-2 text-[12px] text-muted">
        <input
          type="checkbox"
          checked={separateVocals}
          onChange={(e) => setSeparateVocals(e.target.checked)}
        />
        自动分离人声（去伴奏 / 背景音，Demucs；约 1 分钟音频 10 秒）
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
        <Button disabled={!!busy} onClick={() => void run("slice", () => backend.datasetSlice(project.id), () => setScan(null))}>
          {busy === "slice" ? "切片中…" : "切片 5–15s"}
        </Button>
        <Button disabled={!!busy} onClick={() => void run("resample", () => backend.datasetResample(project.id), () => setScan(null))}>
          {busy === "resample" ? "转换中…" : "统一 44.1k 单声道"}
        </Button>
      </div>

      {qc?.assertions && (
        <div className="mt-4 rounded-lg border border-line bg-surface p-3.5">
          <div className="flex items-center justify-between">
            <span className="text-[13px] font-medium">门禁结论</span>
            <span className={qc.assertions.passed ? "text-[12px] text-ok" : "text-[12px] text-danger"}>
              {qc.assertions.passed ? "通过" : `${qc.assertions.blocking.length} 个阻断项`}
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
