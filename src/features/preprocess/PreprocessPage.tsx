import { useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/Button";
import { backend } from "@/lib/backend";
import { useActiveProject } from "@/lib/store";

interface Manifest {
  passed?: boolean;
  failedStep?: string | null;
  counts?: Record<string, number>;
  diffs?: Array<{ kind: string; expected: number; actual: number; missing: number }>;
  minClipSeconds?: number;
  configSha256?: string | null;
}

export function PreprocessPage() {
  const project = useActiveProject();
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [assertion, setAssertion] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [useDiff, setUseDiff] = useState(false);
  const [gate, setGate] = useState<{ trainable: boolean; blocking: string[] } | null>(null);
  const [qc, setQc] = useState<{ exists: boolean; passed: boolean; blocking: number } | null>(null);

  useEffect(() => {
    setQc(null);
    setManifest(null);
    setAssertion(null);
    if (!project) return;
    let cancelled = false;
    void backend
      .datasetQcVerdict(project.id)
      .then((value) => {
        if (!cancelled) setQc(value);
      })
      .catch(() => {
        if (!cancelled) setQc(null);
      });
    return () => {
      cancelled = true;
    };
  }, [project?.id]);

  useEffect(() => {
    void backend
      .doctorCached()
      .then(setGate)
      .catch(() => setGate(null));
  }, []);

  useEffect(() => {
    if (!project) return;
    let cancelled = false;
    void backend
      .preprocessManifest(project.id)
      .then((value) => {
        if (!cancelled) setManifest((value as Manifest) ?? null);
      })
      .catch(() => {
        if (!cancelled) setManifest(null);
      });
    return () => {
      cancelled = true;
    };
  }, [project?.id]);

  async function start() {
    if (!project) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const job = await backend.preprocessStart(project.id, { useDiff, numProcesses: 1 });
      setMessage(`已提交任务 ${job}，可在「任务」页查看进度`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function check() {
    if (!project) return;
    setError(null);
    try {
      setAssertion(await backend.configAssert(project.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  if (!project) {
    return (
      <>
        <PageHeader title="预处理" description="先创建项目并完成 QC。" />
        <div className="rounded-lg border border-dashed border-line p-10 text-center text-[12px] text-muted">
          还没有项目
        </div>
      </>
    );
  }

  const counts = manifest?.counts ?? {};
  const diffs = manifest?.diffs ?? [];

  return (
    <>
      <PageHeader
        title="预处理"
        description="resample → preprocess_flist_config → preprocess_hubert_f0。三步按序执行，失败即停，重跑幂等。"
        actions={
          <div className="flex gap-2">
            <Button disabled={busy} onClick={() => void check()}>
              检查配置
            </Button>
            <Button
              variant="primary"
              disabled={busy || gate?.trainable === false || qc?.passed === false || !qc?.exists}
              onClick={() => void start()}
            >
              {busy ? "提交中…" : "开始预处理"}
            </Button>
          </div>
        }
        badge={<span className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted">{project.name}</span>}
      />

      {qc && (!qc.exists || !qc.passed) && (
        <div className="mb-2 rounded-lg border border-warn/40 bg-warn/5 px-3 py-2 text-[12px] text-warn">
          {qc.exists
            ? `数据集 QC 未通过（${qc.blocking} 个阻断项），预处理被阻断；请先在数据集页修复。`
            : "还没有 QC 报告，先到数据集页运行 QC。"}
        </div>
      )}
      {gate?.trainable === false && (
        <div className="mb-2 rounded-lg border border-warn/40 bg-warn/5 px-3 py-2 text-[12px] text-warn">
          环境自检未通过，预处理入口已禁用。阻断项：{gate.blocking.join("、") || "未知"}；请先在设置页修复并重新自检。
        </div>
      )}
      {message && <p className="mb-2 text-[12px] text-ok">{message}</p>}
      {error && <ErrorNotice error={error} compact />}

      <div className="grid gap-3 lg:grid-cols-[1.2fr_1fr]">
        <div className="rounded-lg border border-line bg-surface p-3.5">
          <div className="flex items-center justify-between">
            <span className="text-[13px] font-medium">产物计数</span>
            <span className={`text-[12px] ${manifest?.passed ? "text-ok" : "text-warn"}`}>
              {manifest ? (manifest.passed ? "一致" : `${diffs.length} 项差异`) : "还没有 manifest"}
            </span>
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2 text-[12px] sm:grid-cols-4">
            {([
              ["wav", "音频"],
              ["soft", "特征"],
              ["f0", "F0"],
              ["spec", "频谱"],
            ] as Array<[string, string]>).map(([key, label]) => (
              <div key={key} className="rounded border border-line/70 px-2 py-1.5">
                <div className="text-[11px] text-muted">{label}</div>
                <div className="tabular">{counts[key] ?? "—"}</div>
              </div>
            ))}
          </div>
          {diffs.length > 0 && (
            <ul className="mt-2 space-y-1 text-[12px] text-danger">
              {diffs.map((d) => (
                <li key={d.kind}>
                  {d.kind}：期望 {d.expected}，实际 {d.actual}，缺 {d.missing}
                </li>
              ))}
            </ul>
          )}
          {manifest?.failedStep && (
            <p className="mt-2 text-[12px] text-danger">失败步骤：{manifest.failedStep}</p>
          )}
          {manifest?.minClipSeconds != null && (
            <p className="mt-2 text-[11px] text-muted">最短切片：{manifest.minClipSeconds}s（扩散 duration 必须更小）</p>
          )}
        </div>

        <div className="rounded-lg border border-line bg-surface p-3.5">
          <div className="text-[13px] font-medium">选项</div>
          <label className="mt-2 flex items-center gap-2 text-[12px] text-muted">
            <input type="checkbox" checked={useDiff} onChange={(e) => setUseDiff(e.target.checked)} />
            同时生成浅扩散产物（mel/vol）
          </label>
          <p className="mt-2 text-[11px] leading-relaxed text-muted">
            预处理进程数固定为 1：每个 worker 都会把语音编码器加载到 GPU，进程数直接吃显存。
          </p>
          {assertion && (
            <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap rounded border border-line/60 p-2 text-[11px] text-muted">
              {JSON.stringify(assertion, null, 2)}
            </pre>
          )}
        </div>
      </div>
    </>
  );
}
