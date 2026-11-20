import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";

type Channel = "alipay" | "wechat";

const CHANNELS: Array<{ id: Channel; label: string; src: string; hint: string }> = [
  { id: "alipay", label: "支付宝", src: "/donate/alipay.jpg", hint: "打开支付宝「扫一扫」" },
  { id: "wechat", label: "微信支付", src: "/donate/wechat.png", hint: "打开微信「扫一扫」" },
];

/** 自愿捐赠弹窗：纯前端展示收款码，不采集信息、不联网。 */
export function DonateDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [channel, setChannel] = useState<Channel>("alipay");
  const active = CHANNELS.find((c) => c.id === channel) ?? CHANNELS[0]!;

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="支持开发"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-xl border border-line bg-surface p-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="text-[14px] font-semibold">支持开发</div>
            <div className="mt-1 text-[11px] leading-relaxed text-muted">
              SVC Studio 完全离线、免费使用。如果你觉得它有用，可以自愿请作者喝杯咖啡——不影响任何功能。
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="rounded-md border border-line px-2 py-0.5 text-[12px] text-muted hover:text-fg"
          >
            ✕
          </button>
        </div>

        <div className="mt-3 flex gap-1">
          {CHANNELS.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => setChannel(c.id)}
              className={`rounded-md border px-2.5 py-1 text-[11px] transition ${
                channel === c.id
                  ? "border-accent/60 bg-accent/10 text-fg"
                  : "border-line text-muted hover:text-fg"
              }`}
            >
              {c.label}
            </button>
          ))}
        </div>

        <div className="mt-3 flex flex-col items-center">
          <img
            src={active.src}
            alt={`${active.label}收款码`}
            className="w-full max-w-[260px] rounded-lg bg-white object-contain"
          />
          <div className="mt-2 text-[11px] text-muted">{active.hint}</div>
        </div>

        <div className="mt-4 flex justify-end">
          <Button size="sm" onClick={onClose}>
            关闭
          </Button>
        </div>
      </div>
    </div>
  );
}
