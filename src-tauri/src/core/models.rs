use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Project {
    pub id: String,
    pub name: String,
    pub slug: String,
    pub root_path: String,
    pub speaker: Option<String>,
    pub speech_encoder: String,
    pub f0_predictor: String,
    pub material_type: String,
    pub gpu_index: Option<i64>,
    pub status: String,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Job {
    pub id: String,
    #[serde(rename = "type")]
    pub kind: String,
    pub project_id: Option<String>,
    pub payload_json: String,
    pub status: String,
    pub progress: f64,
    pub step: Option<i64>,
    pub total: Option<i64>,
    pub error_code: Option<String>,
    pub pid: Option<i64>,
    pub started_at: Option<String>,
    pub finished_at: Option<String>,
    pub parent_job_id: Option<String>,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct JobEvent {
    pub seq: i64,
    pub ts: String,
    pub level: String,
    pub code: Option<String>,
    pub message: String,
    pub data_json: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ModelCard {
    pub id: String,
    pub project_id: Option<String>,
    pub name: String,
    pub step: Option<i64>,
    pub path: String,
    pub sha256: String,
    pub bytes: Option<i64>,
    pub speech_encoder: String,
    pub f0_predictor: Option<String>,
    pub vocoder: Option<String>,
    pub vol_embedding: bool,
    pub config_path: String,
    pub config_sha256: Option<String>,
    pub dataset_fingerprint: Option<String>,
    pub train_run_id: Option<String>,
    pub diffusion_path: Option<String>,
    pub cluster_path: Option<String>,
    pub index_path: Option<String>,
    pub source: String,
    pub status: String,
    pub mixed: bool,
    pub compat_json: Option<String>,
    pub origin_dir: Option<String>,
    pub copied: bool,
    pub required_assets_json: Option<String>,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct DatasetSummary {
    pub id: String,
    pub project_id: String,
    pub version: i64,
    pub root_path: String,
    pub file_count: i64,
    pub total_seconds: f64,
    pub fingerprint: Option<String>,
    pub low_data: bool,
    pub qc_path: Option<String>,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct JobStatusCount {
    pub status: String,
    pub count: i64,
}
