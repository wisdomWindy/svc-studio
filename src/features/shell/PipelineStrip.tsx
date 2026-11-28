import { NavLink } from "react-router-dom";
import { cn } from "@/lib/cn";

/**
 * 产线导引条：按软件**实际**流程排列，不是功能清单。
 *
 * 关键约定：
 * - 第一步是「创建项目」——没有项目就没有数据集/模型目录，后面全都做不了
 * - 「导入」与「质检/切片」是同一条流程：拖入素材后自动跑
 *   分离 → 转码 → 切片 → 44.1k → 精修 → 清理，质检门禁也在其中，因此合成一步
 * - 「登记」是训练完成后的自动动作，单独一步是为了让人知道模型会进模型库
 *
 * 每一步都可点击跳转，当前所在页面高亮。
 */
const STEPS = [
  {
    to: "/projects",
    label: "创建项目",
    hint: "先建项目：素材、模型、日志都落在项目目录里",
  },
  {
    to: "/dataset",
    label: "导入与处理",
    hint: "拖入素材后自动：分离 → 转码 → 切片 → 44.1k → 精修 → 清理（含质检门禁）",
  },
  {
    to: "/preprocess",
    label: "预处理",
    hint: "提取 vec768/hubert 特征与 F0，训练前必须完成",
  },
  {
    to: "/training",
    label: "训练",
    hint: "先 100 步冒烟验证可续训，再正式长训",
  },
  {
    to: "/models",
    label: "登记模型",
    hint: "训练完成自动登记进模型库，也可手动导入/压缩",
  },
  {
    to: "/inference",
    label: "推理",
    hint: "试听、批量转换、导出",
  },
];

export function PipelineStrip() {
  return (
    <div className="drag-region flex h-14 shrink-0 items-center gap-1 border-b border-line bg-surface/60 px-4 backdrop-blur">
      <nav
        aria-label="产线步骤"
        className="no-drag flex w-full items-center gap-1 overflow-x-auto"
      >
        {STEPS.map((s, i) => (
          <div key={s.to} className="flex items-center gap-1">
            <NavLink
              to={s.to}
              title={s.hint}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] transition-colors",
                  isActive
                    ? "border-accent/60 bg-accent/10 text-ink"
                    : "border-line text-muted hover:border-line/80 hover:text-ink",
                )
              }
            >
              {({ isActive }) => (
                <>
                  <span
                    className={cn(
                      "grid size-4 place-items-center rounded-full text-[10px] tabular",
                      isActive ? "bg-accent text-white" : "bg-raised",
                    )}
                  >
                    {i + 1}
                  </span>
                  <span>{s.label}</span>
                </>
              )}
            </NavLink>
            {i < STEPS.length - 1 && <span className="text-line">·</span>}
          </div>
        ))}
      </nav>
    </div>
  );
}
