import { useCallback, useRef, useState } from "react";
import { convertFileSrc } from "@tauri-apps/api/core";
import { Pause, Play } from "lucide-react";
import { isTauri } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Waveform, type WaveformController } from "./ui/Waveform";

export interface AudioPairProps {
  /** 原声路径（本地绝对路径） */
  source: string | null;
  /** 输出路径（本地绝对路径） */
  output: string | null;
}

function assetUrl(path: string | null): string | null {
  if (!path || !isTauri()) return null;
  try {
    return convertFileSrc(path);
  } catch {
    return null;
  }
}

/** A/B 对照：双波形 + 同时起播/暂停，用于快速判断"像不像、跑不跑调"。 */
export function AudioPair({ source, output }: AudioPairProps) {
  const sourceUrl = assetUrl(source);
  const outputUrl = assetUrl(output);
  const sourceCtl = useRef<WaveformController | null>(null);
  const outputCtl = useRef<WaveformController | null>(null);
  const [playing, setPlaying] = useState(false);

  const ready = sourceUrl !== null && outputUrl !== null;

  const toggle = useCallback(() => {
    const next = !playing;
    for (const ctl of [sourceCtl.current, outputCtl.current]) {
      if (!ctl) continue;
      if (next) ctl.play();
      else ctl.pause();
    }
    setPlaying(next);
  }, [playing]);

  return (
    <div className="space-y-2">
      <Waveform
        src={sourceUrl}
        label="原声"
        height={56}
        onReady={(ctl) => {
          sourceCtl.current = ctl;
        }}
      />
      <Waveform
        src={outputUrl}
        label="输出"
        height={56}
        onReady={(ctl) => {
          outputCtl.current = ctl;
        }}
      />

      <div className="flex items-center gap-2">
        <Button size="sm" disabled={!ready} onClick={toggle} aria-label={playing ? "暂停对照" : "播放对照"}>
          {playing ? <Pause className="size-3.5" aria-hidden /> : <Play className="size-3.5" aria-hidden />}
          {playing ? "暂停" : "播放对照"}
        </Button>
        <span className="text-[11px] text-muted">
          两条波形同时起播；用于判断音色相似度与是否跑调。
        </span>
      </div>

      {!isTauri() && (
        <p className="text-[11px] text-muted">浏览器预览下不加载本地音频；桌面端会显示双波形。</p>
      )}
    </div>
  );
}
