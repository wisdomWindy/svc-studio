import { create } from "zustand";
import type { Job, ModelCard, Project } from "@/types";

interface AppState {
  projects: Project[];
  activeProjectId: string | null;
  models: ModelCard[];
  jobs: Job[];
  loading: boolean;
  error: string | null;

  setProjects: (projects: Project[]) => void;
  upsertProject: (project: Project) => void;
  selectProject: (id: string | null) => void;
  setModels: (models: ModelCard[]) => void;
  setJobs: (jobs: Job[]) => void;
  upsertJob: (job: Job) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
}

export const useAppStore = create<AppState>((set) => ({
  projects: [],
  activeProjectId: null,
  models: [],
  jobs: [],
  loading: false,
  error: null,

  setProjects: (projects) =>
    set((state) => ({
      projects,
      activeProjectId:
        state.activeProjectId && projects.some((p) => p.id === state.activeProjectId)
          ? state.activeProjectId
          : (projects[0]?.id ?? null),
    })),
  upsertProject: (project) =>
    set((state) => {
      const exists = state.projects.some((p) => p.id === project.id);
      return {
        projects: exists
          ? state.projects.map((p) => (p.id === project.id ? project : p))
          : [project, ...state.projects],
        activeProjectId: state.activeProjectId ?? project.id,
      };
    }),
  selectProject: (id) => set({ activeProjectId: id }),
  setModels: (models) => set({ models }),
  setJobs: (jobs) => set({ jobs }),
  upsertJob: (job) =>
    set((state) => {
      const exists = state.jobs.some((j) => j.id === job.id);
      return {
        jobs: exists
          ? state.jobs.map((j) => (j.id === job.id ? job : j))
          : [job, ...state.jobs],
      };
    }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error }),
}));

export function useActiveProject(): Project | null {
  return useAppStore((state) =>
    state.activeProjectId
      ? (state.projects.find((p) => p.id === state.activeProjectId) ?? null)
      : null,
  );
}

export function activeProject(state: AppState): Project | null {
  if (!state.activeProjectId) return null;
  return state.projects.find((p) => p.id === state.activeProjectId) ?? null;
}
