/** 数据集处理状态的呈现逻辑（与后端 dataset_state 对应），单独放便于测试。 */

export interface DatasetState {
  preparedAt: string | null;
  keptCount: number | null;
  upToDate: boolean;
}

/** 状态行：明确「进入本页只做质检，不会重跑分离/切片」。 */
export function datasetStateText(state: DatasetState | null): string {
  if (!state?.preparedAt) {
    return "尚未处理：导入素材后会自动跑 分离 → 切片 → 精修；之后再进本页只做质检";
  }
  const when = state.preparedAt.slice(0, 19).replace("T", " ");
  const kept = state.keptCount != null ? ` · 保留 ${state.keptCount} 段` : "";
  const tail = state.upToDate
    ? " · 源文件无变化：进入本页只做质检，不会重复分离/切片"
    : " · 检测到新增源文件，可点「重新处理」只处理这些新文件";
  return `已处理（${when}）${kept}${tail}`;
}

/** 处理按钮文案：已处理过且无新文件时降级为「重新处理（仅新文件）」 */
export function prepareButtonLabel(state: DatasetState | null, busy: boolean): string {
  if (busy) return "处理中…";
  return state?.upToDate ? "重新处理（仅新文件）" : "开始处理数据集";
}
