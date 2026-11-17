import { useEffect, useState } from "react";
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

/**
 * 首次启动向导：安装包不含 Python 运行时（NSIS 32 位限制），
 * 这里引导用户选择 runtime-*.zip 并解压到数据根。
 */
export function RuntimeGate({ onReady }: { onReady: () => void }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
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

  if (!isTauri() || !status || done) return null;

  async function install(path: string) {
    setBusy(true);
    setError(null);
    try {
      const next = await backend.installRuntimeFromZip(path);
      setStatus(next);
      if (next.ready) {
        setDone(true);
        onReady();
      } else {
        setError("解压完成但没有找到可用的 python.exe");
      }
    } catch (e) {
      setError(toErrorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function pick() {
    try {
      const picked = await open({
        multiple: false,
        title: "选择运行时包 runtime-*.zip",
        filters: [{ name: "运行时包", extensions: ["zip"] }],
      });
      if (typeof picked === "string") await install(picked);
    } catch (e) {
      setError(toErrorText(e));
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/80 p-6">
      <div className="w-full max-w-[560px] rounded-xl border border-line bg-surface p-5">
        <h2 className="text-[15px] font-semibold">先安装内置运行时</h2>
        <p className="mt-1 text-[12px] leading-relaxed text-muted">
          安装包不含 Python 运行时（安装器有 2GB 上限）。请选择随包下载的{" "}
          <code className="rounded bg-raised px-1">runtime-0.1.0.zip</code>，解压约需 3–5 分钟。
        </p>

        {status.detectedPack && (
          <div className="mt-3 rounded-lg border border-line bg-raised/40 p-2.5 text-[12px]">
            检测到运行时包：
            <span className="ml-1 break-all text-ink">{status.detectedPack}</span>
            <div className="mt-2">
              <Button size="sm" variant="primary" disabled={busy} onClick={() => void install(status.detectedPack!)}>
                {busy ? "安装中…" : "使用它安装"}
              </Button>
            </div>
          </div>
        )}

        <div className="mt-3 flex items-center gap-2">
          <Button variant="primary" disabled={busy} onClick={() => void pick()}>
            {busy ? "解压中，请稍候…" : "选择 runtime 包"}
          </Button>
          {busy && <span className="text-[11px] text-muted">3.5GB 解压到 7.6GB，不要关闭窗口</span>}
        </div>

        {error && <p className="mt-2 text-[12px] text-danger">{error}</p>}
        <p className="mt-3 text-[11px] text-muted">
          运行时会被解压到数据目录下的 <code>runtime/</code>；训练、推理、预处理都只使用它，不会调用系统 Python。
        </p>
      </div>
    </div>
  );
}
