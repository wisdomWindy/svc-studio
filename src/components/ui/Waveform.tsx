import { useEffect, useRef, useState } from "react";

export interface WaveformController {
  play: () => void;
  pause: () => void;
  getDuration: () => number;
}

export interface WaveformProps {
  src: string | null;
  height?: number;
  label?: string;
  onReady?: (controller: WaveformController) => void;
}

/**
 * 波形只负责"画"，不负责解码整段音频：wavesurfer 内部走 decodeAudioData，
 * 30 分钟素材也不会阻塞主线程。
 */
export function Waveform({ src, height = 64, label, onReady }: WaveformProps) {
  const host = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!src || !host.current) return;
    let disposed = false;
    let destroy: (() => void) | null = null;

    void (async () => {
      try {
        const mod = await import("wavesurfer.js");
        if (disposed || !host.current) return;
        const wavesurfer = mod.default.create({
          container: host.current,
          height,
          waveColor: "#4b5563",
          progressColor: "#d66a3a",
          cursorColor: "#e8ecf3",
          barWidth: 2,
          barGap: 1,
          normalize: true,
        });
        wavesurfer.on("error", (e: Error) => setError(e?.message ?? "无法解码音频"));
        await wavesurfer.load(src);
        onReady?.({
          play: () => void wavesurfer.play(),
          pause: () => wavesurfer.pause(),
          getDuration: () => wavesurfer.getDuration(),
        });
        destroy = () => wavesurfer.destroy();
      } catch (e) {
        setError(e instanceof Error ? e.message : "波形组件加载失败");
      }
    })();

    return () => {
      disposed = true;
      destroy?.();
    };
  }, [src, height, onReady]);

  if (!src) {
    return (
      <div
        className="grid place-items-center rounded-md border border-dashed border-line text-[11px] text-muted"
        style={{ height }}
      >
        {label ? `${label} · 暂无音频` : "暂无音频"}
      </div>
    );
  }

  return (
    <div className="rounded-md border border-line bg-raised/40 px-2 py-1">
      {label && <div className="mb-0.5 text-[10px] text-muted">{label}</div>}
      <div ref={host} style={{ height }} />
      {error && <p className="mt-1 text-[10px] text-danger">{error}</p>}
    </div>
  );
}
