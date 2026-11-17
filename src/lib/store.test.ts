import { beforeEach, describe, expect, it } from "vitest";
import { useAppStore, activeProject } from "./store";
import type { Job, Project } from "@/types";

function project(id: string): Project {
  return {
    id,
    name: `P-${id}`,
    slug: id,
    rootPath: `C:/p/${id}`,
    speaker: null,
    speechEncoder: "vec768l12",
    f0Predictor: "rmvpe",
    materialType: "singing",
    gpuIndex: null,
    status: "active",
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
  };
}

function job(id: string, status: Job["status"]): Job {
  return {
    id,
    type: "train_main",
    projectId: "a",
    status,
    progress: 0,
    step: null,
    total: null,
    errorCode: null,
    startedAt: null,
    finishedAt: null,
  };
}

describe("app store", () => {
  beforeEach(() => {
    useAppStore.setState({
      projects: [],
      activeProjectId: null,
      models: [],
      jobs: [],
      loading: false,
      error: null,
    });
  });

  it("selects the first project when none is active", () => {
    useAppStore.getState().setProjects([project("a"), project("b")]);
    expect(useAppStore.getState().activeProjectId).toBe("a");
  });

  it("keeps an existing selection when the list refreshes", () => {
    useAppStore.getState().setProjects([project("a"), project("b")]);
    useAppStore.getState().selectProject("b");
    useAppStore.getState().setProjects([project("b"), project("c")]);
    expect(useAppStore.getState().activeProjectId).toBe("b");
  });

  it("falls back when the active project disappears", () => {
    useAppStore.getState().setProjects([project("a")]);
    useAppStore.getState().setProjects([project("z")]);
    expect(useAppStore.getState().activeProjectId).toBe("z");
  });

  it("upserts jobs without duplicating", () => {
    useAppStore.getState().upsertJob(job("j1", "running"));
    useAppStore.getState().upsertJob({ ...job("j1", "succeeded") });
    expect(useAppStore.getState().jobs).toHaveLength(1);
    expect(useAppStore.getState().jobs[0]?.status).toBe("succeeded");
  });

  it("exposes the active project helper", () => {
    useAppStore.getState().setProjects([project("a")]);
    expect(activeProject(useAppStore.getState())?.id).toBe("a");
  });
});
