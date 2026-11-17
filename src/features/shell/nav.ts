import {
  FolderKanban,
  ListMusic,
  SlidersHorizontal,
  GraduationCap,
  Mic,
  Library,
  Activity,
  Settings,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  hint: string;
}

export const NAV_ITEMS: NavItem[] = [
  { to: "/projects", label: "项目", icon: FolderKanban, hint: "产线状态与最近任务" },
  { to: "/dataset", label: "数据集", icon: ListMusic, hint: "导入、QC、切片" },
  { to: "/preprocess", label: "预处理", icon: SlidersHorizontal, hint: "三步链路与配置" },
  { to: "/training", label: "训练", icon: GraduationCap, hint: "冒烟、长训、恢复" },
  { to: "/inference", label: "推理", icon: Mic, hint: "试听、批量、导出" },
  { to: "/models", label: "模型库", icon: Library, hint: "登记、导入、压缩" },
  { to: "/tasks", label: "任务", icon: Activity, hint: "队列与日志" },
  { to: "/settings", label: "设置", icon: Settings, hint: "环境、资产、合规" },
];
