-- V001：初始 schema（契约见 docs/requests/intent_770b99fe990a4a0d/prd/appendix-c-data-model.md）
-- 约定：时间一律 UTC ISO8601 文本；布尔用 INTEGER 0/1；JSON 存 TEXT。
-- 所有枚举都用 CHECK 约束，避免脏状态写进库。

CREATE TABLE IF NOT EXISTS projects (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  slug            TEXT NOT NULL UNIQUE,
  root_path       TEXT NOT NULL,
  speaker         TEXT,
  speech_encoder  TEXT NOT NULL,
  f0_predictor    TEXT NOT NULL,
  material_type   TEXT NOT NULL DEFAULT 'singing' CHECK (material_type IN ('singing','speech')),
  gpu_index       INTEGER,
  status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS datasets (
  id              TEXT PRIMARY KEY,
  project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  version         INTEGER NOT NULL,
  root_path       TEXT NOT NULL,
  file_count      INTEGER NOT NULL DEFAULT 0,
  total_seconds   REAL NOT NULL DEFAULT 0,
  fingerprint     TEXT,
  low_data        INTEGER NOT NULL DEFAULT 0 CHECK (low_data IN (0,1)),
  qc_path         TEXT,
  created_at      TEXT NOT NULL,
  UNIQUE (project_id, version)
);

CREATE TABLE IF NOT EXISTS dataset_files (
  id              TEXT PRIMARY KEY,
  dataset_id      TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  rel_path        TEXT NOT NULL,
  seconds         REAL NOT NULL,
  sample_rate     INTEGER NOT NULL,
  channels        INTEGER NOT NULL,
  sha256          TEXT,
  status          TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','quarantine','excluded')),
  issues_json     TEXT
);
CREATE INDEX IF NOT EXISTS idx_dataset_files_dataset_status ON dataset_files(dataset_id, status);

CREATE TABLE IF NOT EXISTS qc_reports (
  id              TEXT PRIMARY KEY,
  dataset_id      TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  json_path       TEXT NOT NULL,
  passed          INTEGER NOT NULL CHECK (passed IN (0,1)),
  blocking_count  INTEGER NOT NULL DEFAULT 0,
  warning_count   INTEGER NOT NULL DEFAULT 0,
  generated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preprocess_runs (
  id              TEXT PRIMARY KEY,
  project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  dataset_id      TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  speech_encoder  TEXT NOT NULL,
  f0_predictor    TEXT NOT NULL,
  use_diff        INTEGER NOT NULL DEFAULT 0,
  vol_aug         INTEGER NOT NULL DEFAULT 0,
  num_processes   INTEGER NOT NULL DEFAULT 1,
  status          TEXT NOT NULL CHECK (status IN ('queued','running','stopping','succeeded','failed','cancelled','interrupted')),
  manifest_path   TEXT,
  started_at      TEXT,
  finished_at     TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
  id              TEXT PRIMARY KEY,
  run_id          TEXT NOT NULL REFERENCES preprocess_runs(id) ON DELETE CASCADE,
  kind            TEXT NOT NULL CHECK (kind IN ('wav','soft','f0','spec','vol','mel','aug_mel','aug_vol')),
  rel_path        TEXT NOT NULL,
  sha256          TEXT,
  bytes           INTEGER,
  counted         INTEGER NOT NULL DEFAULT 1 CHECK (counted IN (0,1))
);
CREATE INDEX IF NOT EXISTS idx_artifacts_run_kind ON artifacts(run_id, kind);

CREATE TABLE IF NOT EXISTS train_runs (
  id              TEXT PRIMARY KEY,
  project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  kind            TEXT NOT NULL CHECK (kind IN ('main','diffusion','cluster','index')),
  config_path     TEXT NOT NULL,
  params_json     TEXT NOT NULL,
  params_hash     TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('queued','running','stopping','stopped','succeeded','failed','cancelled','interrupted')),
  start_step      INTEGER,
  end_step        INTEGER,
  total_steps     INTEGER,
  peak_vram_mb    INTEGER,
  report_path     TEXT,
  job_id          TEXT,
  created_at      TEXT NOT NULL,
  finished_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_train_runs_project ON train_runs(project_id, kind);

CREATE TABLE IF NOT EXISTS checkpoints (
  id              TEXT PRIMARY KEY,
  train_run_id    TEXT NOT NULL REFERENCES train_runs(id) ON DELETE CASCADE,
  step            INTEGER NOT NULL,
  path            TEXT NOT NULL,
  sha256          TEXT,
  bytes           INTEGER,
  created_at      TEXT NOT NULL,
  is_default      INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0,1))
);

CREATE TABLE IF NOT EXISTS models (
  id                    TEXT PRIMARY KEY,
  project_id            TEXT REFERENCES projects(id) ON DELETE SET NULL,
  name                  TEXT NOT NULL,
  step                  INTEGER,
  path                  TEXT NOT NULL,
  sha256                TEXT NOT NULL,
  bytes                 INTEGER,
  speech_encoder        TEXT NOT NULL,
  f0_predictor          TEXT,
  vocoder               TEXT,
  vol_embedding         INTEGER NOT NULL DEFAULT 0 CHECK (vol_embedding IN (0,1)),
  config_path           TEXT NOT NULL,
  config_sha256         TEXT,
  dataset_fingerprint   TEXT,
  train_run_id          TEXT,
  diffusion_path        TEXT,
  cluster_path          TEXT,
  index_path            TEXT,
  source                TEXT NOT NULL DEFAULT 'trained' CHECK (source IN ('trained','imported','mixed')),
  status                TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','compressed','incompatible','missing')),
  mixed                 INTEGER NOT NULL DEFAULT 0 CHECK (mixed IN (0,1)),
  compat_json           TEXT,
  origin_dir            TEXT,
  copied                INTEGER NOT NULL DEFAULT 0 CHECK (copied IN (0,1)),
  required_assets_json  TEXT,
  created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_models_project_status ON models(project_id, status);
CREATE INDEX IF NOT EXISTS idx_models_sha256 ON models(sha256);

CREATE TABLE IF NOT EXISTS inference_runs (
  id              TEXT PRIMARY KEY,
  project_id      TEXT REFERENCES projects(id) ON DELETE SET NULL,
  model_id        TEXT NOT NULL REFERENCES models(id) ON DELETE RESTRICT,
  batch_name      TEXT NOT NULL,
  model_scope     TEXT NOT NULL CHECK (model_scope IN ('project','other','manual','imported')),
  params_json     TEXT NOT NULL,
  params_hash     TEXT NOT NULL,
  input_count     INTEGER NOT NULL DEFAULT 0,
  ok_count        INTEGER NOT NULL DEFAULT 0,
  fail_count      INTEGER NOT NULL DEFAULT 0,
  status          TEXT NOT NULL CHECK (status IN ('queued','running','succeeded','succeeded_with_errors','failed','cancelled')),
  report_path     TEXT,
  job_id          TEXT,
  created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inference_runs_model ON inference_runs(model_id, created_at);

CREATE TABLE IF NOT EXISTS inference_items (
  id              TEXT PRIMARY KEY,
  run_id          TEXT NOT NULL REFERENCES inference_runs(id) ON DELETE CASCADE,
  input_path      TEXT NOT NULL,
  input_sha256    TEXT,
  output_path     TEXT,
  status          TEXT NOT NULL CHECK (status IN ('queued','ok','failed','skipped')),
  error_code      TEXT,
  rtf             REAL,
  peak_dbfs       REAL,
  clip_ratio      REAL,
  seconds_in      REAL,
  seconds_out     REAL
);
CREATE INDEX IF NOT EXISTS idx_inference_items_run_status ON inference_items(run_id, status);

CREATE TABLE IF NOT EXISTS presets (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,
  scope           TEXT NOT NULL CHECK (scope IN ('inference','train')),
  params_json     TEXT NOT NULL,
  builtin         INTEGER NOT NULL DEFAULT 0 CHECK (builtin IN (0,1)),
  created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id              TEXT PRIMARY KEY,
  type            TEXT NOT NULL,
  project_id      TEXT REFERENCES projects(id) ON DELETE SET NULL,
  payload_json    TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('blocked','queued','running','stopping','stopped','succeeded','failed','cancelled','interrupted')),
  progress        REAL NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 1),
  step            INTEGER,
  total           INTEGER,
  error_code      TEXT,
  pid             INTEGER,
  started_at      TEXT,
  finished_at     TEXT,
  parent_job_id   TEXT,
  created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_project_status ON jobs(project_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);

CREATE TABLE IF NOT EXISTS job_events (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id          TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  seq             INTEGER NOT NULL,
  ts              TEXT NOT NULL,
  level           TEXT NOT NULL CHECK (level IN ('debug','info','warn','error')),
  code            TEXT,
  message         TEXT NOT NULL,
  data_json       TEXT,
  UNIQUE (job_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_job_events_job_seq ON job_events(job_id, seq);

CREATE TABLE IF NOT EXISTS assets (
  id              TEXT PRIMARY KEY,
  kind            TEXT NOT NULL CHECK (kind IN ('encoder','f0','vocoder','pretrained','diffusion')),
  key             TEXT NOT NULL,
  version         TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('missing','downloading','verifying','ready','corrupt')),
  bytes           INTEGER,
  sha256          TEXT,
  local_path      TEXT,
  source_url      TEXT,
  verified_at     TEXT,
  UNIQUE (kind, key, version)
);

CREATE TABLE IF NOT EXISTS asset_files (
  id              TEXT PRIMARY KEY,
  asset_id        TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  rel_path        TEXT NOT NULL,
  bytes           INTEGER,
  sha256          TEXT,
  status          TEXT NOT NULL CHECK (status IN ('missing','ok','corrupt')),
  UNIQUE (asset_id, rel_path)
);

CREATE TABLE IF NOT EXISTS consents (
  id              TEXT PRIMARY KEY,
  kind            TEXT NOT NULL CHECK (kind IN ('agpl','usage','dataset','voice')),
  doc_version     TEXT NOT NULL,
  app_version     TEXT NOT NULL,
  subject         TEXT,
  accepted_at     TEXT NOT NULL,
  hash            TEXT
);

CREATE TABLE IF NOT EXISTS settings (
  key             TEXT PRIMARY KEY,
  value_json      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_sessions (
  id                TEXT PRIMARY KEY,
  source_kind       TEXT NOT NULL CHECK (source_kind IN ('files','folder','zip')),
  source_paths_json TEXT NOT NULL,
  staging_dir       TEXT,
  bytes_total       INTEGER NOT NULL DEFAULT 0,
  status            TEXT NOT NULL CHECK (status IN ('incomplete','staging','recognizing','done','rejected','cancelled')),
  model_id          TEXT,
  error_code        TEXT,
  created_at        TEXT NOT NULL,
  finished_at       TEXT
);
