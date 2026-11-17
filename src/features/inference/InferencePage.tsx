import { useCallback, useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { PageHeader } from "@/components/PageHeader";
import { ErrorNotice } from "@/components/ErrorNotice";
import { AudioPair } from "@/components/AudioPair";
import { Button } from "@/components/ui/Button";
import { backend } from "@/lib/backend";
import { useActiveProject, useAppStore } from "@/lib/store";
import { statusLabel } from "@/lib/format";
import { toErrorText } from "@/lib/errors";

const F0 = ["rmvpe", "fcpe", "crepe", "harvest", "dio", "pm"];

export function InferencePage() {
  const project = useActiveProject();
  const { models, setModels } = useAppStore();
  const [modelId, setModelId] = useState<string>("");
  const [inputs, setInputs] = useState<string[]>([]);
  const [trans, setTrans] = useState(0);
  const [f0, setF0] = useState("rmvpe");
  const [clip, setClip] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [batches, setBatches] = useState<Array<Record<string, unknown>>>([]);
  const [batchReport, setBatchReport] = useState<Record<string, unknown> | null>(null);
  const [selectedItem, setSelectedItem] = useState<number>(0);

  const reload = useCallback(async () => {
    try {
      const list = await backend.listModels();
      setModels(list);
      if (!modelId && list[0]) setModelId(list[0].id);
      const current = await backend.defaultModel();
      if (current) setModelId(current);
    } catch {
      /* 浏览器预览下无后端 */
    }
  }, [modelId, setModels]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    setBatches([]);
    if (!project) return;
    let cancelled = false;
    void backend
      .inferBatches(project.id)
      .then((list) => {
        if (!cancelled) setBatches(list);
      })
      .catch(() => {
        if (!cancelled) setBatches([]);
      });
    return () => {
      cancelled = true;
    };
  }, [project?.id, message]);

  async function pickInputs() {
    try {
      const picked = await open({
        multiple: true,
        title: "选择要转换的音频",
        filters: [{ name: "音频", extensions: ["wav", "flac", "mp3", "m4a"] }],
      });
      if (!picked) return;
      setInputs(Array.isArray(picked) ? picked : [picked]);
    } catch {
      setError("无法打开文件选择器（浏览器预览不支持）");
    }
  }

  async function run() {
    if (!project || !modelId || inputs.length === 0) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const params = {
        trans,
        f0Predictor: f0,
        clip: project.materialType === "singing" ? 0 : clip,
        autoPredictF0: false,
        wavFormat: "flac",
      };
      const job = await backend.inferStart(project.id, modelId, inputs, params, null, "batch");
      setMessage(`推理已提交：${job}`);
    } catch (e) {
      setError(toErrorText(e));
    } finally {
      setBusy(false);
    }
  }

  if (!project) {
    return (
      <>
        <PageHeader title="推理" description="先创建项目并准备一个模型。" />
        <div className="rounded-lg border border-dashed border-line p-10 text-center text-[12px] text-muted">
          还没有项目
        </div>
      </>
    );
  }

  const model = models.find((m) => m.id === modelId) ?? null;

  return (
    <>
      <PageHeader
        title="推理"
        description="选模型、选音频、调参数。每次推理落到独立批次目录，报告里留参数快照与模型指纹。"
        actions={
          <Button variant="primary" disabled={busy || !modelId || inputs.length === 0} onClick={() => void run()}>
            {busy ? "提交中…" : `开始推理（${inputs.length}）`}
          </Button>
        }
        badge={<span className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted">{project.name}</span>}
      />

      {message && <p className="mb-2 text-[12px] text-ok">{message}</p>}
      {error && <ErrorNotice error={error} compact />}

      <div className="grid gap-3 lg:grid-cols-[1fr_340px]">
        <div className="space-y-3">
          <div className="rounded-lg border border-line bg-surface p-3.5">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[13px] font-medium">模型</span>
              <select
                className="max-w-[240px] rounded-md border border-line bg-raised px-2 py-1 text-[12px]"
                value={modelId}
                onChange={(e) => setModelId(e.target.value)}
              >
                <option value="">选择模型</option>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}（{statusLabel(m.status)}）
                  </option>
                ))}
              </select>
            </div>
            {model && (
              <p className="mt-1 text-[11px] text-muted">
                {model.speechEncoder}
                {model.f0Predictor ? ` · ${model.f0Predictor}` : ""}
                {model.step != null ? ` · ${model.step} 步` : ""}
              </p>
            )}
          </div>

          <div className="rounded-lg border border-dashed border-line bg-surface/50 p-6 text-center">
            <p className="text-[13px]">{inputs.length > 0 ? `已选 ${inputs.length} 个文件` : "选择要转换的音频"}</p>
            <p className="mt-1 text-[11px] text-muted">支持 wav / flac / mp3 / m4a；进批次前会统一成 44.1k 单声道。</p>
            <Button className="mt-3" onClick={() => void pickInputs()}>
              选择音频
            </Button>
            {inputs.length > 0 && (
              <ul className="mx-auto mt-2 max-h-24 max-w-md overflow-auto text-left text-[11px] text-muted">
                {inputs.map((path) => (
                  <li key={path} className="truncate">
                    {path}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="overflow-hidden rounded-lg border border-line">
            <div className="border-b border-line bg-raised px-3 py-2 text-[12px] text-muted">历史批次</div>
            {batches.length === 0 ? (
              <p className="px-3 py-6 text-center text-[12px] text-muted">还没有批次</p>
            ) : (
              <ul className="divide-y divide-line/60">
                {batches.map((batch) => {
                  const summary = (batch.summary ?? {}) as { ok?: number; failed?: number };
                  return (
                    <li
                      key={String(batch.name)}
                      className="cursor-pointer px-3 py-1.5 text-[12px] hover:bg-raised/40"
                      onClick={() => {
                        setSelectedItem(0);
                        void backend
                          .inferBatchReport(project.id, String(batch.name))
                          .then(setBatchReport)
                          .catch((e) => setError(toErrorText(e)));
                      }}
                    >
                      <div className="flex justify-between gap-2">
                        <span className="truncate">{String(batch.name)}</span>
                        <span className={summary.failed ? "text-warn" : "text-ok"}>
                          ok {summary.ok ?? 0} · 失败 {summary.failed ?? 0}
                        </span>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>

        <div className="rounded-lg border border-line bg-surface p-3.5">
          <div className="text-[13px] font-medium">参数</div>
          <label className="mt-2 block text-[12px]">
            <span className="text-muted">变调（半音）</span>
            <input
              type="number"
              min={-24}
              max={24}
              value={trans}
              onChange={(e) => setTrans(Number(e.target.value))}
              className="mt-1 w-full rounded-md border border-line bg-raised px-2 py-1 text-[12px]"
            />
          </label>
          <label className="mt-2 block text-[12px]">
            <span className="text-muted">F0 预测器</span>
            <select
              className="mt-1 w-full rounded-md border border-line bg-raised px-2 py-1 text-[12px]"
              value={f0}
              onChange={(e) => setF0(e.target.value)}
            >
              {F0.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          {project.materialType === "speech" && (
            <label className="mt-2 block text-[12px]">
              <span className="text-muted">强制切片（秒，0=自动）</span>
              <input
                type="number"
                min={0}
                max={60}
                value={clip}
                onChange={(e) => setClip(Number(e.target.value))}
                className="mt-1 w-full rounded-md border border-line bg-raised px-2 py-1 text-[12px]"
              />
            </label>
          )}
          <p className="mt-2 text-[11px] leading-relaxed text-muted">
            歌声项目默认关闭自动音高（会严重跑调）；whisper 编码器会强制 clip=25、lg=1。
          </p>

          {batchReport && (() => {
            const items = (batchReport.items ?? []) as Array<Record<string, unknown>>;
            const item = items[selectedItem];
            return (
              <div className="mt-3 border-t border-line pt-2">
                <div className="text-[12px] font-medium">批次对照</div>
                <ul className="mt-1 max-h-20 overflow-auto text-[11px]">
                  {items.map((it, index) => (
                    <li key={index}>
                      <button
                        type="button"
                        className={`w-full truncate text-left ${index === selectedItem ? "text-accent" : "text-muted"}`}
                        onClick={() => setSelectedItem(index)}
                      >
                        {String(it.status)} · {String(it.input).split(/[\\\\/]/).pop()}
                      </button>
                    </li>
                  ))}
                </ul>
                <div className="mt-2">
                  <AudioPair
                    source={(item?.input as string) ?? null}
                    output={(item?.output as string) ?? null}
                  />
                </div>
              </div>
            );
          })()}
        </div>
      </div>
    </>
  );
}
