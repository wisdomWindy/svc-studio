import { useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import { backend } from "@/lib/backend";
import { Button } from "@/components/ui/Button";
import { isTauri } from "@/lib/api";
import { toErrorText } from "@/lib/errors";

interface Status {
  ready: boolean;
  pythonPath: string | null;
  detectedPack: string | null;
}

interface Progress {
  stage: string;
  percent: number;
  file?: string;
}

/**
 * 首次启动向导：安装包不含 Python 运行时。
 * 优先「自动下载」（多分卷 + 校验 + 解压），也可手动选择本地 runtime zip。
 */
export function RuntimeGate({ onReady }: { onReady: () => void }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState<"download" | "zip" | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!isTauri()) return;
    void backend
      .runtimeSetupStatus()
      .then((s) => {
        setStatus(s);
        setDone(s.ready);
      })
      .catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    if (!isTauri()) return;
    let unlisten: (() => void) | undefined;
    void listen<Progress>("runtime-progress", (event) => {
      setProgress(event.payload);
    }).then((fn) => {
      unlisten = fn;
    });
    return () => unlisten?.();
  }, []);

  if (!isTauri() || !status || done) return null;

  async function download() {
    setBusy("download");
    setError(null);
    setProgress({ stage: "manifest", percent: 0 });
    try {
      const next = await backend.installRuntimeFromRelease();
      if (next.ready) {
        setDone(true);
        onReady();
      } else {
        setError("下载完成但没有找到可用的 python.exe");
      }
    } catch (e) {
      setError(toErrorText(e));
    } finally {
      setBusy(null);
    }
  }

  async function installZip(path: string) {
    setBusy("zip");
    setError(null);
    try {
      const next = await backend.installRuntimeFromZip(path);
      if (next.ready) {
        setDone(true);
        onReady();
      } else {
        setError("解压完成但没有找到可用的 python.exe");
      }
    } catch (e) {
      setError(toErrorText(e));
    } finally {
      setBusy(null);
    }
  }

  async function pick() {
    try {
      const picked = await open({
        multiple: false,
        title: "选择运行时包 runtime-*.zip",
        filters: [{ name: "运行时包", extensions: ["zip"] }],
      });
      if (typeof picked === "string") await installZip(picked);
    } catch (e) {
      setError(toErrorText(e));
    }
  }

  const stageText =
    progress?.stage === "download"
      ? `下载中 ${progress.percent ?? 0}%${progress.file ? `（${progress.file}）` : ""}`
      : progress?.stage === "extract"
        ? "校验并解压（约 3–5 分钟）"
        : progress?.stage === "done"
          ? "完成"
          : "准备中…";

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/80 p-6">
      <div className="w-full max-w-[560px] rounded-xl border border-line bg-surface p-5">
        <h2 className="text-[15px] font-semibold">先安装内置运行时</h2>
        <p className="mt-1 text-[12px] leading-relaxed text-muted">
          安装包不含 Python 运行时（约 3.5GB）。点下面按钮自动下载并解压到数据目录，也可以手动选择已下载的 zip。
        </p>

        {busy === "download" ? (
          <div className="mt-3 rounded-lg border border-line bg-raised/40 p-3">
            <div className="flex items-center justify-between text-[12px]">
              <span>{stageText}</span>
              <span className="tabular text-muted">{progress?.percent ?? 0}%</span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded bg-line">
              <div
                className="h-full rounded bg-accent transition-all"
                style={{ width: `${Math.min(100, progress?.percent ?? 2)}%` }}
              />
            </div>
            <p className="mt-2 text-[11px] text-muted">下载期间不要关闭窗口；中断后可重试，已下载的部分会复用。</p>
          </div>
        ) : (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button variant="primary" disabled={!!busy} onClick={() => void download()}>
              自动下载运行时（约 3.5GB）
            </Button>
            <Button disabled={!!busy} onClick={() => void pick()}>
              {busy === "zip" ? "解压中…" : "选择本地 zip"}
            </Button>
          </div>
        )}

        {status.detectedPack && busy !== "download" && (
          <div className="mt-3 rounded-lg border border-line bg-raised/40 p-2.5 text-[12px]">
            检测到本地包：
            <span className="ml-1 break-all text-ink">{status.detectedPack}</span>
            <div className="mt-2">
              <Button size="sm" variant="primary" disabled={!!busy} onClick={() => void installZip(status.detectedPack!)}>
                使用它安装
              </Button>
            </div>
          </div>
        )}

        {error && <p className="mt-2 whitespace-pre-wrap text-[12px] text-danger">{error}</p>}
        <p className="mt-3 text-[11px] text-muted">
          运行时解压到数据目录下的 <code>runtime/</code>；训练、推理、预处理都只使用它，不会调用系统 Python。
        </p>
      </div>
    </div>
  );
}
