import { useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/Button";
import { backend } from "@/lib/backend";
import { useAsyncAction } from "@/lib/hooks";
import { useAppStore } from "@/lib/store";
import { formatClock, statusLabel } from "@/lib/format";
import type { CreateProjectInput, Job } from "@/types";

/** 正在跑的任务 → 卡片上显示的短标签 */
const JOB_LABEL: Record<string, string> = {
  dataset_import: "导入中",
  dataset_qc: "质检中",
  dataset_slice: "切片中",
  resample: "重采样中",
  preprocess: "预处理中",
  train_smoke: "冒烟训练中",
  train_main: "训练中",
  train_diffusion: "训练扩散中",
  train_cluster: "聚类中",
  train_index: "建索引中",
  inference: "推理中",
  model_import: "导入模型中",
  model_compress: "压缩模型中",
};

export function isActive(job: Job): boolean {
  return job.status === "running" || job.status === "queued";
}

/** 任务的完成度百分比：优先用 step/total（训练），否则用 progress */
export function jobPercent(job: Job): number {
  if (job.step != null && job.total != null && job.total > 0) {
    return Math.min(100, Math.round((job.step / job.total) * 100));
  }
  return Math.min(100, Math.max(0, Math.round(job.progress)));
}


const ENCODERS = [
  "vec768l12",
  "vec256l9",
  "hubertsoft",
  "whisper-ppg",
  "whisper-ppg-large",
  "cnhubertlarge",
  "dphubert",
  "wavlmbase+",
];
const F0 = ["rmvpe", "fcpe", "crepe", "harvest", "dio", "pm"];

export function OverviewPage() {
  const { projects, activeProjectId, setProjects, upsertProject, selectProject } = useAppStore();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<CreateProjectInput>({
    name: "",
    speaker: "",
    speechEncoder: "vec768l12",
    f0Predictor: "rmvpe",
    materialType: "singing",
  });

  const load = useAsyncAction(backend.listProjects);
  const create = useAsyncAction(backend.createProject);
  const remove = useAsyncAction(backend.deleteProject);
  const [jobs, setJobs] = useState<Job[]>([]);

  useEffect(() => {
    void load.run().then((result) => result && setProjects(result));
    // 卡片要显示「训练中 31%」这类实时进度，所以顺带拉一次任务列表
    void backend
      .listJobs(200)
      .then(setJobs)
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** 该项目当前正在跑的任务（可能多项，取最近启动的一个） */
  function activeJobOf(projectId: string): Job | undefined {
    return jobs
      .filter((job) => job.projectId === projectId && isActive(job))
      .sort((a, b) => (b.startedAt ?? "").localeCompare(a.startedAt ?? ""))[0];
  }

  async function submit() {
    const result = await create.run({
      ...form,
      speaker: form.speaker?.trim() ? form.speaker : null,
    });
    if (result) {
      upsertProject(result.project);
      selectProject(result.project.id);
      setOpen(false);
      setForm({ ...form, name: "" });
    }
  }

  async function del(id: string, name: string) {
    if (!window.confirm(`删除项目「${name}」？工作区文件会一并删除。`)) return;
    const ok = await remove.run(id, true);
    if (ok !== undefined) {
      setProjects(useAppStore.getState().projects.filter((p) => p.id !== id));
    }
  }

  return (
    <>
      <PageHeader
        title="项目"
        description="一个项目 = 一个工作区 = 一条独立的 so-vits-svc 产线。数据、配置、模型都隔离在项目目录内。"
        actions={
          <Button variant="primary" onClick={() => setOpen((v) => !v)}>
            {open ? "收起" : "新建项目"}
          </Button>
        }
        badge={
          <span className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted">
            {projects.length} 个
          </span>
        }
      />

      {open && (
        <div className="mb-4 rounded-lg border border-line bg-surface p-4">
          <div className="grid gap-3 md:grid-cols-2">
            <label className="text-[12px]">
              <span className="text-muted">项目名</span>
              <input
                className="mt-1 w-full rounded-md border border-line bg-raised px-2.5 py-1.5 text-[13px] outline-none focus:border-accent"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="例如：小满的音色"
              />
            </label>
            <label className="text-[12px]">
              <span className="text-muted">说话人名（可留空）</span>
              <input
                className="mt-1 w-full rounded-md border border-line bg-raised px-2.5 py-1.5 text-[13px] outline-none focus:border-accent"
                value={form.speaker ?? ""}
                onChange={(e) => setForm({ ...form, speaker: e.target.value })}
                placeholder="xiaoman"
              />
            </label>
            <label className="text-[12px]">
              <span className="text-muted">语音编码器（训练后不可改）</span>
              <select
                className="mt-1 w-full rounded-md border border-line bg-raised px-2.5 py-1.5 text-[13px]"
                value={form.speechEncoder}
                onChange={(e) => setForm({ ...form, speechEncoder: e.target.value })}
              >
                {ENCODERS.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-[12px]">
              <span className="text-muted">F0 预测器</span>
              <select
                className="mt-1 w-full rounded-md border border-line bg-raised px-2.5 py-1.5 text-[13px]"
                value={form.f0Predictor}
                onChange={(e) => setForm({ ...form, f0Predictor: e.target.value })}
              >
                {F0.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="mt-3 flex items-center gap-4 text-[12px]">
            <span className="text-muted">素材类型</span>
            {(["singing", "speech"] as const).map((v) => (
              <label key={v} className="flex items-center gap-1.5">
                <input
                  type="radio"
                  checked={form.materialType === v}
                  onChange={() => setForm({ ...form, materialType: v })}
                />
                {v === "singing" ? "歌声（禁用自动音高）" : "语音"}
              </label>
            ))}
          </div>

          {create.error && <div className="mt-2"><ErrorNotice error={create.error} compact /></div>}

          <div className="mt-3 flex items-center gap-2">
            <Button
              variant="primary"
              disabled={create.pending || !form.name.trim()}
              onClick={() => void submit()}
            >
              {create.pending ? "创建中…" : "创建"}
            </Button>
            <Button onClick={() => setOpen(false)}>取消</Button>
          </div>
        </div>
      )}

      {load.error && <div className="mb-3"><ErrorNotice error={load.error} compact /></div>}

      {projects.length === 0 ? (
        <div className="rounded-lg border border-dashed border-line bg-surface/50 p-10 text-center">
          <p className="text-[13px]">还没有项目</p>
          <p className="mt-1 text-[12px] text-muted">
            新建一个项目后，就能导入素材、跑质检、训模型。所有文件都落在项目目录里。
          </p>
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {projects.map((project) => {
            const job = activeJobOf(project.id);
            const percent = job ? jobPercent(job) : null;
            return (
              <div
                key={project.id}
                role="button"
                tabIndex={0}
                onClick={() => selectProject(project.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    selectProject(project.id);
                  }
                }}
                className={`rounded-lg border p-3.5 text-left transition-colors ${
                  project.id === activeProjectId
                    ? "border-accent/70 bg-surface"
                    : "border-line bg-surface hover:border-line/80"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[13px] font-medium">{project.name}</span>
                  {job ? (
                    <span className="flex shrink-0 items-center gap-1.5 rounded bg-accent/15 px-1.5 py-0.5 text-[10px] text-accent">
                      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
                      {JOB_LABEL[job.type] ?? "运行中"}
                      {percent != null && <span className="tabular">{percent}%</span>}
                    </span>
                  ) : (
                    <span className="shrink-0 rounded bg-raised px-1.5 py-0.5 text-[10px] text-muted">
                      {statusLabel(project.status)}
                    </span>
                  )}
                </div>
                {job && percent != null && (
                  <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-raised">
                    <div className="h-full rounded-full bg-accent" style={{ width: `${percent}%` }} />
                  </div>
                )}
                <div className="mt-1 truncate text-[11px] text-muted">{project.speechEncoder} · {project.f0Predictor}</div>
                <div className="mt-2 flex items-center justify-between text-[11px] text-muted">
                  <span>{project.materialType === "singing" ? "歌声" : "语音"}</span>
                  <span>{formatClock(project.createdAt)}</span>
                </div>
              <div className="mt-2 flex items-center justify-end gap-3">
                <button
                  type="button"
                  className="rounded px-1.5 py-0.5 text-[11px] text-muted hover:text-accent"
                  onClick={(e) => {
                    e.stopPropagation();
                    void backend.projectOpenDir(project.id).catch(() => undefined);
                  }}
                >
                  打开项目
                </button>
                <button
                  type="button"
                  className="rounded px-1.5 py-0.5 text-[11px] text-muted hover:text-danger"
                  onClick={(e) => {
                    e.stopPropagation();
                    void del(project.id, project.name);
                  }}
                >
                  删除
                </button>
              </div>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
