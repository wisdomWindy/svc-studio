import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppShell } from "@/features/shell/AppShell";
import { OverviewPage } from "@/features/projects/OverviewPage";
import { DatasetPage } from "@/features/dataset/DatasetPage";
import { PreprocessPage } from "@/features/preprocess/PreprocessPage";
import { TrainingPage } from "@/features/training/TrainingPage";
import { InferencePage } from "@/features/inference/InferencePage";
import { ModelsPage } from "@/features/models/ModelsPage";
import { TasksPage } from "@/features/tasks/TasksPage";
import { SettingsPage } from "@/features/settings/SettingsPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/projects" replace /> },
      { path: "projects", element: <OverviewPage /> },
      { path: "dataset", element: <DatasetPage /> },
      { path: "preprocess", element: <PreprocessPage /> },
      { path: "training", element: <TrainingPage /> },
      { path: "inference", element: <InferencePage /> },
      { path: "models", element: <ModelsPage /> },
      { path: "tasks", element: <TasksPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);
