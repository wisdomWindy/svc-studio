/** 与 src-tauri 的 serde 结构手工镜像；改动任一侧必须同步另一侧。 */

export type JobStatus =
  | "blocked"
  | "queued"
  | "running"
  | "stopping"
  | "stopped"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "interrupted";

export type JobKind =
  | "dataset_import"
  | "dataset_qc"
  | "dataset_slice"
  | "resample"
  | "preprocess"
  | "train_smoke"
  | "train_main"
  | "train_diffusion"
  | "train_cluster"
  | "train_index"
  | "inference"
  | "model_import"
  | "model_compress"
  | "onnx_export"
  | "diagnostics";

export interface JobEvent {
  seq: number;
  ts: string;
  level: "debug" | "info" | "warn" | "error";
  code: string | null;
  message: string;
  dataJson: string | null;
}

export interface Job {
  id: string;
  type: JobKind;
  projectId: string | null;
  status: JobStatus;
  progress: number;
  step: number | null;
  total: number | null;
  errorCode: string | null;
  payloadJson?: string;
  startedAt: string | null;
  finishedAt: string | null;
}

export interface AppError {
  code: string;
  severity: "blocking" | "recoverable" | "warning" | "fatal";
  message: string;
  cause: string;
  nextAction: { label: string; deeplink: string | null };
  detail: string | null;
  retriable: boolean;
}

export interface Bootstrap {
  appVersion: string;
  runtimeVersion: string | null;
  runtimeReady: boolean;
  pythonPath: string;
  dbVersion: number;
  dataRoot: string;
  webview2Version: string | null;
  gpu: { name: string; vramMb: number } | null;
  firstRun: boolean;
  probeMs: number;
  reconciledJobs: number;
}

/** 镜像 src-tauri/src/core/models.rs::Project */
export interface Project {
  id: string;
  name: string;
  slug: string;
  rootPath: string;
  speaker: string | null;
  speechEncoder: string;
  f0Predictor: string;
  materialType: "singing" | "speech";
  gpuIndex: number | null;
  status: "active" | "archived";
  createdAt: string;
  updatedAt: string;
}

/** 镜像 src-tauri/src/core/models.rs::ModelCard */
export interface ModelCard {
  id: string;
  projectId: string | null;
  name: string;
  step: number | null;
  path: string;
  sha256: string;
  bytes: number | null;
  speechEncoder: string;
  f0Predictor: string | null;
  vocoder: string | null;
  volEmbedding: boolean;
  configPath: string;
  configSha256: string | null;
  datasetFingerprint: string | null;
  trainRunId: string | null;
  diffusionPath: string | null;
  clusterPath: string | null;
  indexPath: string | null;
  source: "trained" | "imported" | "mixed";
  status: "ready" | "compressed" | "incompatible" | "missing";
  mixed: boolean;
  compatJson: string | null;
  originDir: string | null;
  copied: boolean;
  requiredAssetsJson: string | null;
  createdAt: string;
}

export interface DatasetSummary {
  id: string;
  projectId: string;
  version: number;
  rootPath: string;
  fileCount: number;
  totalSeconds: number;
  fingerprint: string | null;
  lowData: boolean;
  qcPath: string | null;
  createdAt: string;
}

export interface JobStatusCount {
  status: string;
  count: number;
}

export interface CreateProjectInput {
  name: string;
  speaker?: string | null;
  speechEncoder: string;
  f0Predictor: string;
  materialType: "singing" | "speech";
  rootPath?: string | null;
  gpuIndex?: number | null;
}
