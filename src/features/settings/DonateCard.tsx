import { useState } from "react";

type Channel = "alipay" | "wechat";

const CHANNELS: Array<{ id: Channel; label: string; src: string; hint: string }> = [
  { id: "alipay", label: "支付宝", src: "/donate/alipay.jpg", hint: "打开支付宝「扫一扫」" },
  { id: "wechat", label: "微信支付", src: "/donate/wechat.png", hint: "打开微信「扫一扫」" },
];

/** 自愿捐赠：纯前端展示收款码，不采集任何信息、不联网。 */
export function DonateCard() {
  const [channel, setChannel] = useState<Channel>("alipay");
  const active = CHANNELS.find((c) => c.id === channel) ?? CHANNELS[0]!;

  return (
    <div className="rounded-lg border border-line bg-surface p-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-[13px] font-medium">支持开发</div>
          <div className="mt-0.5 text-[11px] text-muted">
            SVC Studio 完全离线、免费使用。如果你觉得它有用，可以自愿请作者喝杯咖啡——不影响任何功能。
          </div>
        </div>
        <div className="flex gap-1">
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
      </div>

      <div className="mt-3 flex flex-col items-center gap-2">
        <img
          src={active.src}
          alt={`${active.label}收款码`}
          className="w-[220px] rounded-lg border border-line bg-white object-contain"
        />
        <span className="text-[11px] text-muted">{active.hint}</span>
      </div>
    </div>
  );
}
