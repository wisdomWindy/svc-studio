import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const datasetQcVerdict = vi.fn();
const preprocessManifest = vi.fn();
const doctorCached = vi.fn();

vi.mock("@/lib/backend", () => ({
  backend: {
    datasetQcVerdict: (id: string) => datasetQcVerdict(id),
    preprocessManifest: (id: string) => preprocessManifest(id),
    doctorCached: () => doctorCached(),
  },
}));

vi.mock("@/lib/store", () => ({
  useActiveProject: () => ({
    id: "prj_test",
    name: "test",
    slug: "test",
    rootPath: "C:/p/test",
    speaker: "spk",
    speechEncoder: "vec768l12",
    f0Predictor: "rmvpe",
    materialType: "singing",
    gpuIndex: null,
    status: "active",
    createdAt: "2026-11-17T00:00:00Z",
    updatedAt: "2026-11-17T00:00:00Z",
  }),
}));

import { PreprocessPage } from "./PreprocessPage";

function renderPage() {
  return render(
    <MemoryRouter>
      <PreprocessPage />
    </MemoryRouter>,
  );
}

describe("PreprocessPage 主按钮引导", () => {
  beforeEach(() => {
    datasetQcVerdict.mockReset();
    preprocessManifest.mockReset();
    doctorCached.mockReset();
    preprocessManifest.mockResolvedValue(null);
    doctorCached.mockResolvedValue({ trainable: true, blocking: [] });
  });

  it("数据集为空时显示「先上传音频」，不显示禁用的开始按钮", async () => {
    datasetQcVerdict.mockResolvedValue({ exists: false, passed: false, blocking: 0 });
    renderPage();

    await waitFor(() => expect(screen.getByText("先上传音频")).toBeTruthy());
    expect(screen.queryByText("开始预处理")).toBeNull();
    expect(screen.getByText(/数据集还是空的/)).toBeTruthy();
  });

  it("QC 未通过时显示「去修复数据集」", async () => {
    datasetQcVerdict.mockResolvedValue({ exists: true, passed: false, blocking: 2 });
    renderPage();

    await waitFor(() => expect(screen.getByText("去修复数据集")).toBeTruthy());
    expect(screen.getByText(/数据集 QC 未通过/)).toBeTruthy();
  });

  it("QC 通过时显示「开始预处理」", async () => {
    datasetQcVerdict.mockResolvedValue({ exists: true, passed: true, blocking: 0 });
    renderPage();

    await waitFor(() => expect(screen.getByText("开始预处理")).toBeTruthy());
    expect(screen.queryByText(/数据集还是空的/)).toBeNull();
  });
});
