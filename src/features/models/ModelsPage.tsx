import { useCallback, useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { Upload } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/Button";
import { backend } from "@/lib/backend";
import { useActiveProject, useAppStore } from "@/lib/store";
import { formatBytes, statusLabel, statusTone } from "@/lib/format";
import type { ModelCard } from "@/types";
import { toErrorText } from "@/lib/errors";

const TONE: Record<string, string> = {
  ok: "text-ok",
  warn: "text-warn",
  danger: "text-danger",
  muted: "text-muted",
};

/** 兼容性徽章：解析导入/检测时写入的 7 步判定报告 */
export function compatBadge(compatJson: string) {
  let verdict = "";
  let missing: unknown[] = [];
  try {
    const parsed = JSON.parse(compatJson) as { verdict?: string; missingKeys?: unknown[] };
    verdict = parsed.verdict ?? "";
    missing = Array.isArray(parsed.missingKeys) ? parsed.missingKeys : [];
  } catch {
    return <span className="rounded bg-raised px-1.5 py-0.5 text-muted">兼容性报告损坏</span>;
  }
  if (verdict === "ready") {
    return <span className="rounded bg-ok/15 px-1.5 py-0.5 text-ok">兼容 · 全部键命中</span>;
  }
  if (verdict === "compressed") {
    return <span className="rounded bg-warn/15 px-1.5 py-0.5 text-warn">兼容 · 已压缩</span>;
  }
  if (verdict === "incompatible") {
    return (
      <span className="rounded bg-danger/15 px-1.5 py-0.5 text-danger">
        不兼容{missing.length > 0 ? ` · 缺 ${missing.length} 项` : ""}
      </span>
    );
  }
  return <span className="rounded bg-warn/15 px-1.5 py-0.5 text-warn">未知（{verdict || "无判定"}）</span>;
}

export function ModelsPage() {
  const project = useActiveProject();
  const { models, setModels } = useAppStore();
  const [defaultId, setDefaultId] = useState<string | null>(null);
  const [trust, setTrust] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [mixResult, setMixResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const [list, current] = await Promise.all([backend.listModels(), backend.defaultModel()]);
      setModels(list);
      setDefaultId(current);
    } catch (e) {
      setError(toErrorText(e));
    }
  }, [setModels]);

  useEffect(() => {
    void reload();
  }, [reload, project?.id]);

  async function pickAndImport() {
    setError(null);
    setMessage(null);
    if (!trust) {
      setError("请先勾选「我确认模型来自可信来源」");
      return;
    }
    let paths: string[] = [];
    try {
      const picked = await open({
        multiple: true,
        title: "选择 1 个模型 + 1 个 config.json",
        filters: [
          { name: "模型与配置", extensions: ["pth", "pt", "ckpt", "json"] },
          { name: "全部文件", extensions: ["*"] },
        ],
      });
      if (!picked) return;
      paths = Array.isArray(picked) ? picked : [picked];
    } catch (e) {
      setError(e instanceof Error ? e.message : "无法打开文件选择器（浏览器预览不支持）");
      return;
    }

    setBusy(true);
    try {
      const result = await backend.importModel(paths, trust);
      const verdict = String((result.compat as { verdict?: string }).verdict ?? "ready");
      setMessage(`已导入 ${result.model.name}（${statusLabel(verdict)}）`);
      if (result.ignored.length > 0) {
        setMessage((m) => `${m}；忽略 ${result.ignored.length} 个非模型文件`);
      }
      await reload();
    } catch (e) {
      const text =
        typeof e === "object" && e && "message" in e
          ? `${(e as { message: string }).message}${(e as { cause?: string }).cause ? `：${(e as { cause?: string }).cause}` : ""}`
          : toErrorText(e);
      setError(text);
    } finally {
      setBusy(false);
    }
  }

  async function makeDefault(id: string) {
    try {
      await backend.setDefaultModel(id);
      setDefaultId(id);
    } catch (e) {
      setError(toErrorText(e));
    }
  }

  async function remove(model: ModelCard) {
    if (!window.confirm(`删除模型「${model.name}」？该模型的推理历史记录会一并移除，已生成的音频文件保留。`)) return;
    try {
      await backend.deleteModel(model.id);
      await reload();
    } catch (e) {
      setError(toErrorText(e));
    }
  }

  return (
    <>
      <PageHeader
        title="模型库"
        description="模型是全局资产：本项目训的、别处来的，都放在这里。上传即复制到受管目录，源文件不动。"
        badge={
          <span className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted">
            {models.length} 个
          </span>
        }
      />

      <div className="rounded-lg border border-dashed border-line bg-surface/50 p-6 text-center">
        <Upload className="mx-auto size-5 text-muted" aria-hidden />
        <p className="mt-2 text-[13px]">选择 1 个模型 + 1 个 config.json</p>
        <p className="mt-1 text-[12px] text-muted">
          支持 .pth / .pt / .ckpt；一次只能导入一个模型，不做批量。
        </p>
        <label className="mt-3 flex items-center justify-center gap-1.5 text-[12px] text-muted">
          <input type="checkbox" checked={trust} onChange={(e) => setTrust(e.target.checked)} />
          我确认模型来自可信来源（.pth 是 pickle，加载可能执行代码）
        </label>
        <Button className="mt-3" variant="primary" disabled={busy} onClick={() => void pickAndImport()}>
          {busy ? "导入中…" : "选择文件并导入"}
        </Button>
        {message && <p className="mt-2 text-[12px] text-ok">{message}</p>}
        {error && <ErrorNotice error={error} compact />}
      </div>

      {selected.length >= 2 && (
        <div className="mt-3 flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-[12px]">
          <span className="text-muted">已选 {selected.length} 个模型</span>
          <Button
            size="sm"
            onClick={() => {
              setMixResult(null);
              void backend
                .mixPreview(selected.map((id) => ({ modelId: id, weight: 1 })))
                .then((r) =>
                  setMixResult(
                    r.passed
                      ? `可混合；归一权重 ${(r.weights as number[]).join(" / ")}`
                      : `不可混合：${(r.blocking as Array<{ message: string }>).map((b) => b.message).join("；")}`,
                  ),
                )
                .catch((e) => setMixResult(toErrorText(e)));
            }}
          >
            校验混合
          </Button>
          {mixResult && <span className="text-muted">{mixResult}</span>}
        </div>
      )}

      {models.length === 0 ? (
        <p className="mt-4 text-center text-[12px] text-muted">还没有模型。训练完成或在上面导入一个。</p>
      ) : (
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {models.map((model) => (
            <div key={model.id} className="rounded-lg border border-line bg-surface p-3.5">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-[13px] font-medium">{model.name}</div>
                  <div className="mt-0.5 text-[11px] text-muted">
                    {model.speechEncoder}
                    {model.f0Predictor ? ` · ${model.f0Predictor}` : ""}
                    {model.step != null ? ` · ${model.step} 步` : ""}
                  </div>
                </div>
                <span className={`shrink-0 text-[11px] ${TONE[statusTone(model.status)]}`}>
                  {statusLabel(model.status)}
                  {defaultId === model.id ? " · 默认" : ""}
                </span>
              </div>
              <div className="mt-2 flex items-center justify-between text-[11px] text-muted">
                <span>{formatBytes(model.bytes)}</span>
                <span className="uppercase">{model.source}</span>
              </div>

              {/* 关联资产 + 兼容性：来自导入/检测时的 7 步判定（compat_json），没有就是未检测 */}
              <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
                <span>关联：</span>
                <span className={model.diffusionPath ? "text-ok" : "text-muted"}>
                  扩散 {model.diffusionPath ? "✓" : "—"}
                </span>
                <span className={model.clusterPath ? "text-ok" : "text-muted"}>
                  聚类 {model.clusterPath ? "✓" : "—"}
                </span>
                <span className={model.indexPath ? "text-ok" : "text-muted"}>
                  索引 {model.indexPath ? "✓" : "—"}
                </span>
                <span className="ml-auto">
                  {model.compatJson ? (
                    compatBadge(model.compatJson)
                  ) : (
                    <span className="rounded bg-raised px-1.5 py-0.5 text-muted">兼容性未检测</span>
                  )}
                </span>
              </div>

              <div className="mt-2.5 flex flex-wrap gap-1.5">
                <Button size="sm" disabled={defaultId === model.id} onClick={() => void makeDefault(model.id)}>
                  设为默认
                </Button>
                <Button
                  size="sm"
                  onClick={() => {
                    setMessage(null);
                    setError(null);
                    void backend
                      .modelCompat(model.id)
                      .then(() => {
                        setMessage(`已检测「${model.name}」的兼容性`);
                        return reload();
                      })
                      .catch((e) => setError(toErrorText(e)));
                  }}
                >
                  检测兼容性
                </Button>
                <Button
                  size="sm"
                  onClick={() => {
                    setMessage(null);
                    setError(null);
                    void backend
                      .compressModel(model.id)
                      .then((r) => setMessage(`已压缩：${String((r.model as { path?: string })?.path ?? "")}`))
                      .catch((e) => setError(toErrorText(e)));
                  }}
                >
                  压缩
                </Button>
                <Button
                  size="sm"
                  onClick={() => {
                    setMessage(null);
                    setError(null);
                    void backend
                      .modelOnnx(model.id)
                      .then((r) => setMessage(r.ok ? "ONNX 已导出" : `ONNX 失败：${String(r.error ?? "")}`))
                      .catch((e) => setError(toErrorText(e)));
                  }}
                >
                  导出 ONNX
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    setSelected((prev) =>
                      prev.includes(model.id) ? prev.filter((id) => id !== model.id) : [...prev, model.id],
                    )
                  }
                >
                  {selected.includes(model.id) ? "取消混合" : "加入混合"}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => void remove(model)}>
                  删除
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
