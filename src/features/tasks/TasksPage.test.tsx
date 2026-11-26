import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Job } from "@/types";

const listJobs = vi.fn();
const deleteJobs = vi.fn();
const jobEvents = vi.fn();

vi.mock("@/lib/backend", () => ({
  backend: {
    listJobs: () => listJobs(),
    deleteJobs: (ids: string[]) => deleteJobs(ids),
    jobEvents: (id: string) => jobEvents(id),
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

import { TasksPage } from "./TasksPage";

function job(id: string, projectId: string | null, type: Job["type"] = "inference"): Job {
  return {
    id,
    type,
    projectId,
    status: "succeeded",
    progress: 1,
    step: null,
    total: null,
    errorCode: null,
    payloadJson: "{}",
    startedAt: "2026-11-26T02:03:15.463Z",
    finishedAt: "2026-11-26T02:04:15.463Z",
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <TasksPage />
    </MemoryRouter>,
  );
}

/** 取第 index 行（0 是表头），越界直接失败而不是静默返回 undefined。 */
function rowAt(index: number): HTMLElement {
  const row = screen.getAllByRole("row")[index];
  if (!row) throw new Error(`找不到第 ${index} 行`);
  return row;
}

/** 有状态的假后端：删除真的把行从库里拿掉，listJobs 返回当前剩余的行。 */
function useFakeDb(initial: Job[]) {
  let db = [...initial];
  listJobs.mockImplementation(() => Promise.resolve([...db]));
  deleteJobs.mockImplementation((ids: string[]) => {
    db = db.filter((j) => !ids.includes(j.id));
    return Promise.resolve({ deleted: ids.length, refused: [], outputFailed: [] });
  });
  return {
    get db() {
      return [...db];
    },
  };
}

describe("TasksPage 删除", () => {
  beforeEach(() => {
    listJobs.mockReset();
    deleteJobs.mockReset();
    jobEvents.mockReset();
    jobEvents.mockResolvedValue({ events: [], nextSeq: 0 });
    // 删除推理任务会弹确认（产物一并删除），默认放行
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  it("删除当前选中的任务后，右侧日志不再显示它的记录", async () => {
    useFakeDb([job("job_a", "prj_test")]);
    jobEvents.mockResolvedValue({
      events: [
        {
          seq: 1,
          ts: "2026-11-26T03:11:35.000Z",
          level: "info",
          code: null,
          message: "开始预处理",
          dataJson: null,
        },
      ],
      nextSeq: 1,
    });

    renderPage();
    await waitFor(() => expect(screen.getByText("inference")).toBeTruthy());

    // 选中该行，日志面板出现它的事件
    fireEvent.click(screen.getByText("inference"));
    await waitFor(() => expect(screen.getByText("开始预处理")).toBeTruthy());

    fireEvent.click(within(rowAt(1)).getByText("删除"));

    // 任务行与它的日志一起消失：已删任务的记录不能留在面板上
    await waitFor(() => expect(screen.queryByText("inference")).toBeNull());
    expect(screen.queryByText("开始预处理")).toBeNull();
  });

  it("删除后刷新仍按当前项目过滤，不冒出其他项目的任务", async () => {
    const mine = job("job_mine", "prj_test", "inference");
    const other = job("job_other", "prj_other", "preprocess");
    useFakeDb([mine, other]);

    renderPage();
    await waitFor(() => expect(screen.getByText("inference")).toBeTruthy());
    expect(screen.queryByText("preprocess")).toBeNull();

    fireEvent.click(within(rowAt(1)).getByText("删除"));

    await waitFor(() => expect(screen.queryByText("inference")).toBeNull());
    expect(screen.queryByText("preprocess")).toBeNull();
  });

  it("删除后晚到的旧列表不会把已删的任务刷回界面", async () => {
    const a = job("job_a", "prj_test", "inference");
    const b = job("job_b", "prj_test", "preprocess");
    const fake = useFakeDb([a, b]);
    let pending: ((list: Job[]) => void) | null = null;
    let calls = 0;
    listJobs.mockImplementation(() => {
      calls += 1;
      if (calls === 1) return Promise.resolve(fake.db);
      // 第二次（第一次删除触发的刷新）故意挂着，模拟一份旧快照还在路上
      if (calls === 2) return new Promise<Job[]>((resolve) => (pending = resolve));
      return Promise.resolve(fake.db);
    });

    renderPage();
    await waitFor(() => expect(screen.getByText("preprocess")).toBeTruthy());

    // 第一次删除：b 行消失，但这次刷新（旧快照）尚未返回
    fireEvent.click(within(rowAt(2)).getByText("删除"));
    await waitFor(() => expect(screen.queryByText("preprocess")).toBeNull());

    // 第二次删除：a 行消失，这次刷新返回空列表
    fireEvent.click(within(rowAt(1)).getByText("删除"));
    await waitFor(() => expect(screen.queryByText("inference")).toBeNull());

    // 旧快照此刻才返回，必须被丢弃，否则两行都会「复活」
    await act(async () => {
      pending?.([a, b]);
    });
    expect(screen.queryByText("preprocess")).toBeNull();
    expect(screen.queryByText("inference")).toBeNull();
  });

  it("删除推理任务会先确认；取消确认则一条都不删", async () => {
    const a = job("job_a", "prj_test", "inference");
    useFakeDb([a]);
    vi.mocked(window.confirm).mockReturnValueOnce(false);

    renderPage();
    await waitFor(() => expect(screen.getByText("inference")).toBeTruthy());

    fireEvent.click(within(rowAt(1)).getByText("删除"));

    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(String(vi.mocked(window.confirm).mock.calls[0]?.[0])).toContain("音频与报告会一并删除");
    expect(deleteJobs).not.toHaveBeenCalled();
    expect(screen.getByText("inference")).toBeTruthy();
  });

  it("产物删不掉时保留记录并给出提示", async () => {
    const a = job("job_a", "prj_test", "inference");
    useFakeDb([a]);
    deleteJobs.mockResolvedValue({ deleted: 0, refused: [], outputFailed: ["job_a"] });

    renderPage();
    await waitFor(() => expect(screen.getByText("inference")).toBeTruthy());

    fireEvent.click(within(rowAt(1)).getByText("删除"));

    await waitFor(() => expect(screen.getByText(/产物删不掉/)).toBeTruthy());
    // 记录必须留在列表里，不能出现「记录没了、文件还在」
    expect(screen.getByText("inference")).toBeTruthy();
  });
});
