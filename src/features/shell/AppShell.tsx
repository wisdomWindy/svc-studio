import { NavLink, Outlet } from "react-router-dom";
import { useEffect, useState } from "react";
import { cn } from "@/lib/cn";
import { call, isTauri } from "@/lib/api";
import type { Bootstrap } from "@/types";
import { NAV_ITEMS } from "./nav";
import { PipelineStrip } from "./PipelineStrip";
import { ConsentGate } from "@/components/ConsentGate";
import { RuntimeGate } from "@/components/RuntimeGate";
import { runProbe } from "@/lib/probe";
import { backend } from "@/lib/backend";

export function AppShell() {
  const [boot, setBoot] = useState<Bootstrap | null>(null);

  useEffect(() => {
    let alive = true;
    if (!isTauri()) {
      setBoot({
        appVersion: "0.1.0-dev",
        runtimeVersion: null,
        runtimeReady: false,
        pythonPath: "",
        dbVersion: 0,
        dataRoot: "(browser preview)",
        webview2Version: null,
        gpu: null,
        firstRun: true,
        probeMs: 5000,
        reconciledJobs: 0,
      });
      return;
    }
    call<Bootstrap>("bootstrap")
      .then((b) => alive && setBoot(b))
      .catch(() => alive && setBoot(null));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="flex h-full bg-canvas">
      <a
        href="#main"
        className="sr-only rounded-md bg-surface px-3 py-1.5 text-[12px] text-ink focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:border focus:border-accent"
      >
        跳到主内容
      </a>
      <aside className="flex w-[220px] shrink-0 flex-col border-r border-line bg-surface">
        <div className="drag-region flex h-14 items-center gap-2 px-4">
          <div className="grid size-7 place-items-center rounded-md bg-accent text-[13px] font-bold text-white">
            S
          </div>
          <div className="no-drag">
            <div className="text-[13px] font-semibold leading-tight">SVC Studio</div>
            <div className="text-[11px] leading-tight text-muted">
              {boot ? `v${boot.appVersion}` : "启动中…"}
            </div>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto px-2 py-2" aria-label="主导航">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                cn(
                  "group mb-0.5 flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] transition-colors",
                  isActive
                    ? "bg-raised text-ink"
                    : "text-muted hover:bg-raised/60 hover:text-ink",
                )
              }
            >
              {({ isActive }) => (
                <>
                  <item.icon
                    className={cn("size-4", isActive ? "text-accent" : "text-muted")}
                    aria-hidden
                  />
                  <span className="flex-1">{item.label}</span>
                  <span className="hidden text-[10px] text-muted/70 group-hover:block">
                    {item.hint.split("、")[0]}
                  </span>
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-line px-3 py-2 text-[11px] text-muted">
          <div className="truncate" title={boot?.dataRoot}>
            数据目录：{boot?.dataRoot ?? "—"}
          </div>
          <div className="truncate">
            运行时：{boot?.runtimeVersion ?? (boot?.runtimeReady ? "内置运行时已就绪" : "未就绪")}
            {boot?.gpu ? ` · ${boot.gpu.name} ${Math.round(boot.gpu.vramMb / 1024)}GB` : ""}
          </div>
        </div>
      </aside>

      <RuntimeGate onReady={() => undefined} />
      <ConsentGate onComplete={() => undefined} />
      <StartupProbe />
      <div className="flex min-w-0 flex-1 flex-col">
        <PipelineStrip />
        <main id="main" tabIndex={-1} className="min-h-0 flex-1 overflow-y-auto px-6 py-5 outline-none">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

/**
 * 仅当后端以 SVCSTUDIO_PROBE=1 启动时跑一次主线程延迟探针，并把结果落盘。
 * 正常启动完全不走这段代码，不影响冷启动预算。
 */
function StartupProbe() {
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const info = await backend.bootstrap();
        if (cancelled || !info.probe) return;
        const summary = await runProbe({
          durationMs: info.probeMs ?? 5000,
          intervalMs: 20,
          stress: () => backend.jobsSummary(),
        });
        if (!cancelled) await backend.saveProbeResult(summary);
      } catch {
        // 探针失败不影响应用
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  return null;
}