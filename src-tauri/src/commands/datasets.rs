use tauri::State;

use crate::core::models::DatasetSummary;
use crate::core::repo::datasets;
use crate::error::AppResult;
use crate::AppPaths;

#[tauri::command]
pub fn list_datasets(
    state: State<'_, AppPaths>,
    project_id: String,
) -> AppResult<Vec<DatasetSummary>> {
    state.db.with(|c| datasets::list_by_project(c, &project_id))
}
