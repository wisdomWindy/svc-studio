// 首屏 JS 预算：dist/assets/*.js 的 gzip 总和 ≤ 180KB（spec §12）。
import { readdirSync, readFileSync, statSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { join } from "node:path";

const LIMIT = 180 * 1024;
const dir = "dist/assets";
let files;
try {
  files = readdirSync(dir).filter((f) => f.endsWith(".js"));
} catch {
  console.error("dist/assets 不存在，先运行 pnpm build");
  process.exit(1);
}

let total = 0;
for (const f of files) {
  const p = join(dir, f);
  const raw = statSync(p).size;
  const gz = gzipSync(readFileSync(p)).length;
  total += gz;
  console.log(`${f}  raw=${(raw / 1024).toFixed(1)}KB  gz=${(gz / 1024).toFixed(1)}KB`);
}
console.log(`total gzip = ${(total / 1024).toFixed(1)}KB / limit ${LIMIT / 1024}KB`);
if (total > LIMIT) {
  console.error("FAIL: 超预算");
  process.exit(1);
}
console.log("PASS");
