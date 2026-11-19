import { useEffect, useState } from "react";
import { check, type Update } from "@tauri-apps/plugin-updater";
import { relaunch } from "@tauri-apps/plugin-process";
import { Button } from "@/components/ui/Button";
import { toErrorText } from "@/lib/errors";
import { isTauri } from "@/lib/api";

/**
 * 检查更新：读 release 上的 latest.json，下载签名过的安装包并就地安装。
 * 只在桌面端可用；浏览器预览直接隐藏。
 */
export function UpdateChecker() {
  const [update, setUpdate] = useState<Update | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isTauri()) return;
    void (async () => {
      try {
        const next = await check();
        if (next) setUpdate(next);
      } catch {
        // 静默：离线或还没发布过 latest.json 都不是错误
      }
    })();
  }, []);

  if (!isTauri()) return null;

  async function install() {
    if (!update) return;
    setBusy(true);
    setError(null);
    setStatus("下载中…");
    try {
      let downloaded = 0;
      let total = 0;
      await update.downloadAndInstall((event) => {
        if (event.event === "Started" && event.data.contentLength) {
          total = event.data.contentLength;
          setStatus(`下载安装包 0%`);
        } else if (event.event === "Progress") {
          downloaded += event.data.chunkLength;
          const pct = total ? Math.round((downloaded / total) * 100) : 0;
          setStatus(`下载安装包 ${pct}%`);
        } else if (event.event === "Finished") {
          setStatus("安装中，完成后会自动重启…");
        }
      });
      await relaunch();
    } catch (e) {
      setError(toErrorText(e));
      setStatus("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      {update ? (
        <>
          <span className="text-[12px] text-muted">
            新版本 {update.version} 可用
          </span>
          <Button variant="primary" disabled={busy} onClick={() => void install()}>
            {busy ? status || "更新中…" : "下载并安装"}
          </Button>
        </>
      ) : (
        <Button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              const next = await check();
              if (next) setUpdate(next);
              else setStatus("已是最新版本");
            } catch (e) {
              setError(toErrorText(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          检查更新
        </Button>
      )}
      {status && !error && <span className="text-[12px] text-ok">{status}</span>}
      {error && <span className="text-[12px] text-danger">{error}</span>}
    </div>
  );
}
