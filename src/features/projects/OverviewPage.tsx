import { useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/Button";
import { backend } from "@/lib/backend";
import { useAsyncAction } from "@/lib/hooks";
import { useAppStore } from "@/lib/store";
import { formatClock, statusLabel } from "@/lib/format";
import type { CreateProjectInput } from "@/types";

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

  useEffect(() => {
    void load.run().then((result) => result && setProjects(result));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
          {projects.map((project) => (
            <button
              key={project.id}
              title={`打开 ${project.name} 的项目目录`}
              onClick={() => {
                selectProject(project.id);
                void backend.projectOpenDir(project.id).catch(() => undefined);
              }}
              className={`rounded-lg border p-3.5 text-left transition-colors ${
                project.id === activeProjectId
                  ? "border-accent/70 bg-surface"
                  : "border-line bg-surface hover:border-line/80"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-[13px] font-medium">{project.name}</span>
                <span className="shrink-0 rounded bg-raised px-1.5 py-0.5 text-[10px] text-muted">
                  {statusLabel(project.status)}
                </span>
              </div>
              <div className="mt-1 truncate text-[11px] text-muted">{project.speechEncoder} · {project.f0Predictor}</div>
              <div className="mt-2 flex items-center justify-between text-[11px] text-muted">
                <span>{project.materialType === "singing" ? "歌声" : "语音"}</span>
                <span>{formatClock(project.createdAt)}</span>
              </div>
              <div className="mt-1 text-[10px] text-muted">点击卡片打开项目目录</div>
              <div className="mt-2 flex justify-end">
                <span
                  role="button"
                  tabIndex={0}
                  className="rounded px-1.5 py-0.5 text-[11px] text-muted hover:text-danger"
                  onClick={(e) => {
                    e.stopPropagation();
                    void del(project.id, project.name);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.stopPropagation();
                      void del(project.id, project.name);
                    }
                  }}
                >
                  删除
                </span>
              </div>
            </button>
          ))}
        </div>
      )}
    </>
  );
}
