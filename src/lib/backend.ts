import { call } from "./api";
import type {
  CreateProjectInput,
  Job,
  JobEvent,
  ModelCard,
  Project,
  DatasetSummary,
} from "@/types";

/** 与 src-tauri/src/commands 一一对应；命名保持 camelCase（Tauri 会转 snake_case）。 */

export interface BootstrapInfo {
  appVersion: string;
  runtimeVersion: string | null;
  runtimeReady: boolean;
  pythonPath: string;
  dbVersion: number;
  dataRoot: string;
  projectsRoot: string;
  webview2Version: string | null;
  gpu: { name: string; vramMb: number } | null;
  firstRun: boolean;
  probe: boolean;
  probeMs: number;
  reconciledJobs: number;
}

export interface CreateProjectResult {
  project: Project;
  createdDirs: string[];
}

export interface DoctorItem {
  id: string;
  state: "pass" | "warn" | "fail";
  message: string;
  cause: string;
  fix: string;
  detail: Record<string, unknown>;
  blocking: boolean;
}

export interface DoctorReport {
  schema: string;
  generatedAt: string;
  dataRoot: string;
  projectsRoot: string;
  items: DoctorItem[];
  summary: { pass: number; warn: number; fail: number; trainable: boolean };
}

/** 导入后的素材带宽体检：素材上限即模型输出亮度上限。 */
export interface BandwidthReport {
  schema: string;
  files: number;
  failed: string[];
  buckets: Record<string, number>;
  medianRolloffHz: number | null;
  minRolloffHz?: number | null;
  maxRolloffHz?: number | null;
  estimatedOutputCeilingHz: number | null;
  nyquistHz?: number[];
  samplingRateLimitedFiles?: number;
  verdict: { level: string; label: string; advice: string };
  seconds?: number;
}

export interface Recommend {
  vramMb: number;
  batchSize: number;
  diffusionBatch: number;
  diffusionDuration: number;
  numProcesses: number;
  reason: string;
  trainable: boolean;
}

export interface CheckpointInfo {
  smoke: boolean;
  step: number;
  bytes: number;
  path: string;
  modified: string;
}

export interface RegisterModelResult {
  alreadyRegistered: boolean;
  model: ModelCard;
  becameDefault: boolean;
}

export interface ImportResult {
  model: ModelCard;
  compat: Record<string, unknown>;
  ignored: string[];
}

export interface ManifestView {
  signature: "verified" | "unsigned" | "invalid";
  generatedAt: string;
  assetCount: number;
  assets: Array<{
    id: string;
    kind: string;
    key: string;
    version: string;
    required: boolean;
    sourceUrl: string | null;
    licenseNote: string | null;
    files: Array<{ relPath: string; bytes: number; sha256: string; state: unknown }>;
  }>;
}

export const backend = {
  bootstrap: () => call<BootstrapInfo>("bootstrap"),

  listProjects: () => call<Project[]>("list_projects"),
  createProject: (input: CreateProjectInput) =>
    call<CreateProjectResult>("create_project", { input }),
  projectOpenDir: (id: string) => call<void>("project_open_dir", { id }),
  deleteProject: (id: string, deleteFiles: boolean) =>
    call<void>("delete_project", { id, deleteFiles }),

  listDatasets: (projectId: string) =>
    call<DatasetSummary[]>("list_datasets", { projectId }),
  datasetImport: (projectId: string, paths: string[]) =>
    call<{ imported: number; skipped: number; rejected: number; targetDir: string }>(
      "dataset_import",
      { projectId, paths },
    ),
  datasetPrepare: (projectId: string, separate = true) =>
    call<{ separated: unknown[]; separatedSeconds: number | null; converted: unknown[]; skipped: unknown[]; sliced: unknown[]; resampled: unknown[]; keptCount: number | null; removedCount: number }>(
      "dataset_prepare",
      { projectId, separate },
    ),
  // 扫描要遍历目录并读文件头，数据多时会超过默认 15s
  datasetScan: (projectId: string, timeoutMs = 0) =>
    call<Record<string, unknown>>("dataset_scan", { projectId }, timeoutMs),
  // QC 走后台任务，进度通过 dataset-progress 事件回传（kind="qc"）
  datasetQc: (projectId: string) => call<void>("dataset_qc", { projectId }),
  // 导入后立即可查：素材带宽 → 模型输出亮度上限预估
  datasetBandwidth: (projectId: string) =>
    call<BandwidthReport>("dataset_bandwidth", { projectId }),
  datasetSlice: (
    projectId: string,
    opts: { minSeconds?: number; maxSeconds?: number; minIntervalMs?: number } = {},
  ) =>
    call<Record<string, unknown>>("dataset_slice", {
      projectId,
      minSeconds: opts.minSeconds ?? null,
      maxSeconds: opts.maxSeconds ?? null,
      minIntervalMs: opts.minIntervalMs ?? null,
    }),
  datasetResample: (projectId: string) =>
    call<Record<string, unknown>>("dataset_resample", { projectId }),

  preprocessStart: (
    projectId: string,
    opts: { useDiff?: boolean; volAug?: boolean; numProcesses?: number } = {},
  ) =>
    call<string>("preprocess_start", {
      projectId,
      useDiff: opts.useDiff ?? null,
      volAug: opts.volAug ?? null,
      numProcesses: opts.numProcesses ?? null,
    }),
  preprocessManifest: (projectId: string) =>
    call<Record<string, unknown> | null>("preprocess_manifest", { projectId }),
  datasetQcVerdict: (projectId: string) =>
    call<{ exists: boolean; passed: boolean; blocking: number }>("dataset_qc_verdict", {
      projectId,
    }),
  configAssert: (projectId: string) =>
    call<Record<string, unknown>>("config_assert", { projectId }),

  trainRecommend: (vramMb?: number | null) =>
    call<Recommend>("train_recommend", { vramMb: vramMb ?? null }),
  trainStart: (projectId: string, smoke = false, resume = false, kind: "main" | "diffusion" = "main") =>
    call<string>("train_start", { projectId, smoke, resume, kind }),
  trainStop: (jobId: string) => call<boolean>("train_stop", { jobId }),
  trainReset: (projectId: string) => call<string>("train_reset", { projectId }, 10 * 60 * 1000),
  trainCheckpoints: (projectId: string) =>
    call<CheckpointInfo[]>("train_checkpoints", { projectId }),
  // 聚类/索引要跑几十秒到几分钟，远超默认 15s IPC；这里放宽到 30 分钟
  trainAux: (projectId: string, kind: "cluster" | "index", timeoutMs = 0) =>
    call<Record<string, unknown>>("train_aux", { projectId, kind }, timeoutMs),
  registerModel: (projectId: string, checkpoint: string, name?: string) =>
    call<RegisterModelResult>("register_model", {
      input: { projectId, checkpoint, name: name ?? null },
    }),

  listModels: () => call<ModelCard[]>("list_models"),
  defaultModel: () => call<string | null>("default_model"),
  setDefaultModel: (id: string) => call<void>("set_default_model", { id }),
  deleteModel: (id: string) => call<void>("delete_model", { id }),
  compressModel: (modelId: string, half = false) =>
    call<Record<string, unknown>>("model_compress", { modelId, half }),
  modelOnnx: (modelId: string) =>
    call<Record<string, unknown>>("model_onnx", { modelId }),
  mixPreview: (modelsInput: Array<{ modelId: string; weight: number }>, mode = "convex") =>
    call<Record<string, unknown>>("mix_preview", { modelsInput, mode }),

  modelCheck: (modelPath: string, configPath: string) =>
    call<Record<string, unknown>>("model_check", { modelPath, configPath }),
  /** 按模型 id 跑一次 7 步兼容性检测，并把结果写回模型卡 */
  modelCompat: (id: string) => call<Record<string, unknown>>("model_compat", { id }),
  importModel: (paths: string[], trust: boolean) =>
    call<ImportResult>("import_model_upload", { paths, trust }),

  inferStart: (
    projectId: string,
    modelId: string,
    inputs: string[],
    params: Record<string, unknown>,
    spk: string | null,
    batchName: string | null,
  ) =>
    call<string>("infer_start", {
      projectId,
      modelId,
      inputs,
      params,
      spk,
      batchName,
    }),
  inferBatches: (projectId: string) =>
    call<Array<Record<string, unknown>>>("infer_batches", { projectId }),
  inferBatchReport: (projectId: string, name: string) =>
    call<Record<string, unknown>>("infer_batch_report", { projectId, name }),

  listJobs: (limit = 100) => call<Job[]>("list_jobs", { limit }),
  getJob: (id: string) => call<Job | null>("get_job", { id }),
  jobEvents: (jobId: string, afterSeq = 0, limit = 500) =>
    call<{ events: JobEvent[]; nextSeq: number }>("job_events", {
      jobId,
      afterSeq,
      limit,
    }),
  jobsSummary: () =>
    call<{ counts: Array<{ status: string; count: number }>; running: number }>(
      "jobs_summary",
    ),
  jobOpenOutput: (id: string) => call<string>("job_open_output", { id }),
  jobOpenFolder: (id: string) => call<string>("job_open_folder", { id }),
  cancelJob: (id: string) => call<Job>("cancel_job", { id }),
  retryJob: (id: string) => call<Job>("retry_job", { id }),
  deleteJobs: (ids: string[]) =>
    call<{ deleted: number; refused: string[]; outputFailed: string[] }>("delete_jobs", { ids }),
  gpuBusy: () => call<boolean>("gpu_busy"),

  doctorReport: (encoder?: string, f0?: string) =>
    call<DoctorReport>("doctor_report", {
      encoder: encoder ?? null,
      f0: f0 ?? null,
    }),
  saveProbeResult: (result: unknown) =>
    call<string>("save_probe_result", { result }),
  runtimeSetupStatus: () =>
    call<{ ready: boolean; pythonPath: string | null; detectedPack: string | null }>(
      "runtime_setup_status",
    ),
  installRuntimeFromZip: (zipPath: string) =>
    call<{ ready: boolean; pythonPath: string | null; detectedPack: string | null }>(
      "install_runtime_from_zip",
      { zipPath },
    ),

  /// 立即返回；进度与结果通过 runtime-progress 事件回传
  downloadMissingWeights: () => call<void>("download_missing_weights"),
  missingWeights: () => call<string[]>("missing_weights"),
  installRuntimeFromRelease: () => call<void>("install_runtime_from_release"),
  doctorCached: () =>
    call<{ trainable: boolean; blocking: string[] } | null>("doctor_cached"),
  exportDiagnostics: () =>
    call<Record<string, unknown>>("export_diagnostics"),

  assetsManifest: () => call<ManifestView>("assets_manifest"),
  importAssetDir: (assetId: string, sourceDir: string) =>
    call<Record<string, unknown>>("import_asset_dir", {
      input: { assetId, sourceDir },
    }),

  consentsStatus: () =>
    call<{ accepted: string[]; complete: boolean; docVersion: string }>("consents_status"),
  acceptConsents: (kinds: string[]) =>
    call<{ accepted: string[]; complete: boolean; docVersion: string }>("accept_consents", {
      kinds,
    }),

  getSettings: () =>
    call<{ values: Record<string, unknown> }>("get_settings"),
  setSetting: (key: string, value: unknown) =>
    call<void>("set_setting", { update: { key, value } }),
};
