import { useEffect, useRef, useState } from "react";
import { backend } from "@/lib/backend";
import { Button } from "@/components/ui/Button";

const ITEMS: Array<{ kind: string; title: string; body: string }> = [
  {
    kind: "agpl",
    title: "AGPL-3.0 许可",
    body: "本软件包含 so-vits-svc（AGPL-3.0）。源码与构建方式随安装包提供；修改过的版本同样以 AGPL 分发。",
  },
  {
    kind: "usage",
    title: "上游使用规约",
    body: "仅供交流学习；发布到平台须注明输入源；禁止违法、宗教与政治用途。",
  },
  {
    kind: "dataset",
    title: "数据集授权",
    body: "我确认对导入的音频拥有训练与转换所需权利，不使用未授权素材。",
  },
  {
    kind: "voice",
    title: "真人声音告知",
    body: "禁止未经许可克隆他人声音；由使用者自行承担由此产生的法律责任。",
  },
];

/** 首次启动门禁（A2）：四项全部勾选前，训练/推理入口保持禁用。 */
export function ConsentGate({ onComplete }: { onComplete: () => void }) {
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needed, setNeeded] = useState(false);
  const firstBox = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (needed) firstBox.current?.focus();
  }, [needed]);

  useEffect(() => {
    void backend
      .consentsStatus()
      .then((state) => setNeeded(!state.complete))
      .catch(() => setNeeded(false));
  }, []);

  if (!needed) return null;

  const allChecked = ITEMS.every((item) => checked[item.kind]);

  async function accept() {
    setBusy(true);
    setError(null);
    try {
      const state = await backend.acceptConsents(ITEMS.map((i) => i.kind));
      if (state.complete) {
        setNeeded(false);
        onComplete();
      } else {
        setError("确认未全部记录，请重试");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-6">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="consent-title"
        className="w-full max-w-[560px] rounded-xl border border-line bg-surface p-5"
      >
        <h2 id="consent-title" className="text-[15px] font-semibold">
          使用前请确认
        </h2>
        <p className="mt-1 text-[12px] leading-relaxed text-muted">
          这四项确认会记录版本号与时间；未完成前训练与推理入口不可用。
        </p>

        <div className="mt-3 space-y-2.5">
          {ITEMS.map((item) => (
            <label key={item.kind} className="flex gap-2.5 rounded-lg border border-line p-2.5">
              <input
                ref={item === ITEMS[0] ? firstBox : undefined}
                type="checkbox"
                className="mt-0.5"
                checked={!!checked[item.kind]}
                onChange={(e) => setChecked({ ...checked, [item.kind]: e.target.checked })}
              />
              <span className="min-w-0">
                <span className="block text-[12px] text-ink">{item.title}</span>
                <span className="mt-0.5 block text-[11px] leading-relaxed text-muted">{item.body}</span>
              </span>
            </label>
          ))}
        </div>

        {error && <p className="mt-2 text-[12px] text-danger">{error}</p>}

        <div className="mt-4 flex items-center justify-between">
          <span className="text-[11px] text-muted">文档版本 v1</span>
          <Button variant="primary" disabled={!allChecked || busy} onClick={() => void accept()}>
            {busy ? "记录中…" : "确认并继续"}
          </Button>
        </div>
      </div>
    </div>
  );
}
