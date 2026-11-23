import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { invoke } = vi.hoisted(() => ({ invoke: vi.fn() }));

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { backend } from "./backend";

describe("backend command wrappers", () => {
  beforeEach(() => {
    // api.call 在浏览器环境会短路；测试里伪装成 Tauri 运行时。
    Object.defineProperty(window, "__TAURI_INTERNALS__", { value: {}, configurable: true });
    invoke.mockReset();
    invoke.mockResolvedValue(null);
  });

  afterEach(() => {
    Reflect.deleteProperty(window, "__TAURI_INTERNALS__");
  });

  it("uses snake_case command names with camelCase args", async () => {
    await backend.listProjects();
    expect(invoke).toHaveBeenCalledWith("list_projects", undefined);

    await backend.datasetQc("prj_1");
    expect(invoke).toHaveBeenCalledWith("dataset_qc", { projectId: "prj_1" });

    await backend.trainStart("prj_1", true, true);
    expect(invoke).toHaveBeenCalledWith("train_start", {
      projectId: "prj_1",
      smoke: true,
      resume: true,
      kind: "main",
    });
  });

  it("passes struct inputs nested under their parameter name", async () => {
    await backend.createProject({
      name: "x",
      speechEncoder: "vec768l12",
      f0Predictor: "rmvpe",
      materialType: "singing",
    });
    expect(invoke).toHaveBeenCalledWith("create_project", {
      input: expect.objectContaining({ name: "x" }),
    });

    await backend.importAssetDir("contentvec", "C:/assets");
    expect(invoke).toHaveBeenCalledWith("import_asset_dir", {
      input: { assetId: "contentvec", sourceDir: "C:/assets" },
    });
  });

  it("sends job event paging args", async () => {
    invoke.mockResolvedValue({ events: [], nextSeq: 0 });
    await backend.jobEvents("job_1", 5, 200);
    expect(invoke).toHaveBeenCalledWith("job_events", {
      jobId: "job_1",
      afterSeq: 5,
      limit: 200,
    });
  });

  it("wraps import with trust flag", async () => {
    await backend.importModel(["a.pth", "config.json"], true);
    expect(invoke).toHaveBeenCalledWith("import_model_upload", {
      paths: ["a.pth", "config.json"],
      trust: true,
    });
  });
});
