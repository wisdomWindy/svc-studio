import { useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/Button";
import { backend } from "@/lib/backend";
import { useActiveProject } from "@/lib/store";
import { formatDuration, statusLabel } from "@/lib/format";
import { DatasetDropZone } from "./DatasetDropZone";

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
  const [scan, setScan] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [importNote, setImportNote] = useState<string | null>(null);

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
      // 导入后自动：转码 → 切片 → 统一响度 → 扫描 → QC
      const prepared = await backend.datasetPrepare(project.id);
      const convertedCount = Array.isArray(prepared.converted) ? prepared.converted.length : 0;
      const slicedCount = Array.isArray(prepared.sliced) ? prepared.sliced.length : 0;
      if (convertedCount > 0 || slicedCount > 0) {
        setImportNote(
          `${parts.join("，")}；已转码 ${convertedCount} 个、切片 ${slicedCount} 个为 44.1k 单声道 wav`,
        );
      }
      const scanned = await backend.datasetScan(project.id);
      setScan(scanned);
      setQc(await backend.datasetQc(project.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
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
      setError(e instanceof Error ? e.message : String(e));
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
            <Button variant="primary" disabled={!!busy} onClick={() => void run("qc", () => backend.datasetQc(project.id), setQc)}>
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

      {importNote && (
        <p className="mt-2 text-[12px] text-ok">
          {importNote}
          <span className="ml-1 text-muted">（已刷新结构扫描与 QC）</span>
        </p>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          ["总时长", total ? formatDuration(total) : "—"],
          ["文件数", qc?.summary?.fileCount ?? "—"],
          ["静音比", metrics?.silenceRatio != null ? `${(metrics.silenceRatio * 100).toFixed(1)}%` : "—"],
          ["削波比", metrics?.clippingRatio != null ? `${(metrics.clippingRatio * 100).toFixed(3)}%` : "—"],
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
