import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "@/components/PageHeader";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/Button";
import { UpdateChecker } from "./UpdateChecker";
import { DonateDialog } from "./DonateDialog";
import { backend, type BootstrapInfo, type DoctorReport } from "@/lib/backend";
import { toErrorText } from "@/lib/errors";

const TONE: Record<string, string> = {
  pass: "text-ok",
  warn: "text-warn",
  fail: "text-danger",
};
const LABEL: Record<string, string> = { pass: "通过", warn: "警告", fail: "失败" };

export function SettingsPage() {
  const [boot, setBoot] = useState<BootstrapInfo | null>(null);
  const [doctor, setDoctor] = useState<DoctorReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [donateOpen, setDonateOpen] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();

  // 侧边栏「支持作者」带 donate=1 进来时自动弹窗
  useEffect(() => {
    if (searchParams.get("donate") === "1") {
      setDonateOpen(true);
      const next = new URLSearchParams(searchParams);
      next.delete("donate");
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  useEffect(() => {
    void backend
      .bootstrap()
      .then(setBoot)
      .catch(() => setBoot(null));
    void backend
      .getSettings()
      .then((value) => {
        const stored = value.values.theme;
        if (stored === "light" || stored === "dark") setTheme(stored);
      })
      .catch(() => undefined);
  }, []);

  async function runDoctor() {
    setBusy(true);
    setError(null);
    try {
      setDoctor(await backend.doctorReport());
    } catch (e) {
      setError(toErrorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function exportDiagnostics() {
    setBusy(true);
    setError(null);
    try {
      const result = await backend.exportDiagnostics();
      setMessage(`诊断包已生成：${String(result.path ?? "")}`);
    } catch (e) {
      setError(toErrorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.dataset.theme = next;
    try {
      await backend.setSetting("theme", next);
    } catch {
      /* 浏览器预览下忽略 */
    }
  }

  return (
    <>
      <PageHeader
        title="设置"
        description="环境状态、诊断包、外观与合规。默认零联网、零遥测。"
        actions={
          <div className="flex gap-2">
            <UpdateChecker />
            <Button disabled={busy} onClick={() => void runDoctor()}>
              {busy ? "检查中…" : "环境自检"}
            </Button>
            <Button disabled={busy} onClick={() => setDonateOpen(true)}>
              支持开发
            </Button>
            <Button disabled={busy} onClick={() => void exportDiagnostics()}>
              导出诊断包
            </Button>
          </div>
        }
      />

      {message && <p className="mb-2 text-[12px] text-ok">{message}</p>}
      {error && <ErrorNotice error={error} compact />}

      <div className="grid gap-3 lg:grid-cols-[1fr_1fr]">
        <div className="rounded-lg border border-line bg-surface p-3.5">
          <div className="text-[13px] font-medium">环境</div>
          <dl className="mt-2 space-y-1.5 text-[12px]">
            {[
              ["应用版本", boot?.appVersion ?? "—"],
              ["数据目录", boot?.dataRoot ?? "—"],
            ["项目目录", boot?.projectsRoot ?? "—"],
              ["数据库版本", boot ? String(boot.dbVersion) : "—"],
              ["运行时", boot?.runtimeVersion ?? "未就绪"],
              ["GPU", boot?.gpu ? `${boot.gpu.name} ${Math.round(boot.gpu.vramMb / 1024)}GB` : "未检测"],
              ["启动对账", boot ? `${boot.reconciledJobs} 个任务被标记中断` : "—"],
            ].map(([key, value]) => (
              <div key={key} className="flex justify-between gap-3">
                <dt className="shrink-0 text-muted">{key}</dt>
                <dd className="truncate tabular">{value}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-3 flex items-center justify-between border-t border-line pt-2 text-[12px]">
            <span className="text-muted">主题</span>
            <Button size="sm" onClick={() => void toggleTheme()}>
              {theme === "dark" ? "切换到浅色" : "切换到深色"}
            </Button>
          </div>
        </div>

        <div className="rounded-lg border border-line bg-surface p-3.5">
          <div className="flex items-center justify-between">
            <span className="text-[13px] font-medium">自检结果</span>
            {doctor && (
              <span className={doctor.summary.trainable ? "text-[12px] text-ok" : "text-[12px] text-danger"}>
                {doctor.summary.trainable ? "可训练" : "训练被阻断"}
              </span>
            )}
          </div>
          {!doctor && <p className="mt-2 text-[12px] text-muted">点上面的「环境自检」获取九项状态。</p>}
          {doctor && (
            <ul className="mt-2 space-y-1.5 text-[12px]">
              {doctor.items.map((item) => (
                <li key={item.id} className="flex gap-2">
                  <span className={`w-12 shrink-0 ${TONE[item.state]}`}>{LABEL[item.state]}</span>
                  <span className="min-w-0">
                    <span className="text-ink">{item.id}</span>
                    <span className="text-muted"> · {item.message}</span>
                    {item.state !== "pass" && item.fix && (
                      <span className="mt-0.5 block text-[11px] text-muted">修复：{item.fix}</span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <p className="mt-3 text-[11px] leading-relaxed text-muted">
        本软件包含 so-vits-svc（AGPL-3.0）。源码与构建方式随安装包提供；不内置任何第三方模型权重；诊断包不包含音频、特征或权重。
      </p>
      <DonateDialog open={donateOpen} onClose={() => setDonateOpen(false)} />

    </>
  );
}
