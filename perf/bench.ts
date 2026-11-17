// 性能基线采集：产物体积（可自动）+ 启动/路由/IPC（需运行态，后续由 e2e 回填）。
// 结果写入 perf/baseline.json，CI 对比本次与基线，超过 5% 即阻断（spec §12）。
import { readdirSync, readFileSync, statSync, writeFileSync, mkdirSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { join } from "node:path";

interface AssetMetric {
  file: string;
  rawBytes: number;
  gzipBytes: number;
}

function assets(): AssetMetric[] {
  try {
    return readdirSync("dist/assets")
      .filter((f) => f.endsWith(".js") || f.endsWith(".css"))
      .map((f) => {
        const p = join("dist/assets", f);
        const buf = readFileSync(p);
        return { file: f, rawBytes: statSync(p).size, gzipBytes: gzipSync(buf).length };
      });
  } catch {
    return [];
  }
}

const list = assets();
const jsGzip = list.filter((a) => a.file.endsWith(".js")).reduce((s, a) => s + a.gzipBytes, 0);
const cssGzip = list.filter((a) => a.file.endsWith(".css")).reduce((s, a) => s + a.gzipBytes, 0);

const baseline = {
  capturedAt: new Date().toISOString(),
  bundle: {
    jsGzipKb: +(jsGzip / 1024).toFixed(1),
    cssGzipKb: +(cssGzip / 1024).toFixed(1),
    assets: list,
  },
  // 运行态指标：由 scripts/e2e/*.ps1 回填，未采集时为 null（不得写假数字）。
  runtime: {
    coldStartMs: null as number | null,
    interactiveMs: null as number | null,
    routeSwitchMs: null as number | null,
    ipcRoundTripMs: null as number | null,
  },
};

mkdirSync("perf", { recursive: true });
writeFileSync("perf/baseline.json", JSON.stringify(baseline, null, 2) + "\n", "utf8");
console.log(`bundle js=${baseline.bundle.jsGzipKb}KB css=${baseline.bundle.cssGzipKb}KB`);
console.log("perf/baseline.json written");
