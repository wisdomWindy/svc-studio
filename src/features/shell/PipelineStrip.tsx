import { cn } from "@/lib/cn";

/** 7 步产线状态：可跳过、可偏离，不是强制清单（页面设计 §4 风险 1）。 */
const STEPS = [
  { id: "import", label: "导入", hint: "拖入素材" },
  { id: "qc", label: "质检", hint: "可跳过" },
  { id: "slice", label: "切片", hint: "建议" },
  { id: "preprocess", label: "预处理", hint: "必须" },
  { id: "train", label: "训练", hint: "必须" },
  { id: "register", label: "登记", hint: "自动" },
  { id: "infer", label: "推理", hint: "目标" },
];

export function PipelineStrip() {
  return (
    <div className="drag-region flex h-14 shrink-0 items-center gap-1 border-b border-line bg-surface/60 px-4 backdrop-blur">
      <div className="no-drag flex w-full items-center gap-1 overflow-x-auto">
        {STEPS.map((s, i) => (
          <div key={s.id} className="flex items-center gap-1">
            <div
              className={cn(
                "flex items-center gap-1.5 rounded-full border border-line px-2.5 py-1 text-[12px]",
                "text-muted",
              )}
              title={s.hint}
            >
              <span className="grid size-4 place-items-center rounded-full bg-raised text-[10px] tabular">
                {i + 1}
              </span>
              <span>{s.label}</span>
            </div>
            {i < STEPS.length - 1 && <span className="text-line">·</span>}
          </div>
        ))}
      </div>
    </div>
  );
}
