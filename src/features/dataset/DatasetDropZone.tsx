import { useEffect, useRef, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { toErrorText } from "@/lib/errors";

export interface ImportOutcome {
  imported: number;
  skipped: number;
  rejected: number;
  targetDir: string;
}

const AUDIO_EXTS = ["wav", "flac", "mp3", "m4a", "aac", "ogg", "opus", "wma", "aiff", "aif"];

/**
 * 数据集导入区：点按钮开资源管理器，或直接把文件/文件夹拖进来。
 * 选中的内容会被复制到受管目录 `dataset_raw/<说话人>/`，不做格式转换。
 */
export function DatasetDropZone({
  disabled,
  busy,
  onImport,
}: {
  disabled: boolean;
  busy: boolean;
  onImport: (paths: string[]) => void;
}) {
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const activeRef = useRef(true);

  useEffect(() => {
    activeRef.current = true;
    let unlisten: (() => void) | undefined;
    void (async () => {
      try {
        unlisten = await getCurrentWebview().onDragDropEvent((event) => {
          if (!activeRef.current) return;
          if (event.payload.type === "enter" || event.payload.type === "over") {
            setOver(true);
          } else if (event.payload.type === "leave") {
            setOver(false);
          } else if (event.payload.type === "drop") {
            setOver(false);
            const paths = event.payload.paths ?? [];
            if (paths.length > 0) onImport(paths);
          }
        });
      } catch {
        // 浏览器预览环境没有 Tauri webview，忽略
      }
    })();
    return () => {
      activeRef.current = false;
      unlisten?.();
    };
  }, [onImport]);

  async function pickFiles() {
    setError(null);
    try {
      const picked = await open({
        multiple: true,
        title: "选择音频文件",
        filters: [{ name: "音频", extensions: AUDIO_EXTS }],
      });
      const paths = Array.isArray(picked) ? picked : picked ? [picked] : [];
      if (paths.length > 0) onImport(paths);
    } catch (e) {
      setError(toErrorText(e));
    }
  }

  async function pickFolder() {
    setError(null);
    try {
      const picked = await open({ directory: true, multiple: false, title: "选择音频文件夹" });
      if (typeof picked === "string") onImport([picked]);
    } catch (e) {
      setError(toErrorText(e));
    }
  }

  return (
    <div
      className={[
        "rounded-lg border border-dashed p-6 text-center transition-colors",
        over ? "border-accent bg-accent/5" : "border-line bg-surface",
        disabled ? "opacity-60" : "",
      ].join(" ")}
      onDragOver={(e) => e.preventDefault()}
    >
      <div className="text-[13px] font-medium">把音频拖到这里，或从资源管理器选择</div>
      <p className="mt-1 text-[11px] leading-relaxed text-muted">
        会复制到项目的 <code className="rounded bg-raised px-1">dataset_raw/&lt;说话人&gt;/</code>，原文件不动。
        支持 wav / flac / mp3 / m4a / ogg 等。
      </p>
      <div className="mt-3 flex items-center justify-center gap-2">
        <button
          type="button"
          disabled={disabled || busy}
          onClick={() => void pickFiles()}
          className="rounded-md border border-line bg-raised px-3 py-1.5 text-[12px] font-medium hover:border-accent disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "导入中…" : "选择音频文件"}
        </button>
        <button
          type="button"
          disabled={disabled || busy}
          onClick={() => void pickFolder()}
          className="rounded-md border border-line px-3 py-1.5 text-[12px] text-muted hover:border-accent hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
        >
          选择文件夹
        </button>
      </div>
      {error && <p className="mt-2 text-[12px] text-danger">{error}</p>}
    </div>
  );
}
