# 发布打包：构建 → 收集产物 → SHA256 → SBOM → 许可证 → 源码归档。
# 用法：powershell -File scripts/release.ps1 [-Version 0.1.0] [-DryRun]

param(
  [string]$Version = "0.1.0",
  [switch]$DryRun,
  # 跳过构建与 staging 前置：仅生成 SBOM / 许可证 / 源码归档（用于合规材料自检）
  [switch]$SkipBuild,
  # 把 runtime 一起打进安装包（>2GB 会被 NSIS 32 位限制拒绝，默认关闭）
  [switch]$WithRuntime
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$out = "artifacts/$Version"
function Write-Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

Write-Step "检查前置"
$checks = [ordered]@{
  "package.json"          = Test-Path "package.json"
  "src-tauri/Cargo.toml"  = Test-Path "src-tauri/Cargo.toml"
  "resources staged"      = (-not $WithRuntime) -or (Test-Path "src-tauri/resources/MANIFEST.json")
  "AGPL 原文"             = Test-Path "vendor/so-vits-svc/LICENSE"
}
foreach ($key in $checks.Keys) {
  $mark = if ($checks[$key]) { "OK " } else { "缺失" }
  Write-Host ("- {0}: {1}" -f $key, $mark)
}

Write-Step "计划"
Write-Host "1) pnpm build（前端产物）"
Write-Host "2) pnpm tauri build（NSIS 安装包，目标 artifacts/$Version）"
Write-Host "3) 生成 SHA256SUMS.txt"
Write-Host "4) 生成 SBOM.json（CycloneDX 1.5，依赖来自 package.json + Cargo.toml）"
Write-Host "5) 生成 licenses/（AGPL 全文、第三方清单、源码获取方式）"
Write-Host "6) 生成 source-$Version.zip（源码归档，满足 AGPL §6）"

if ($DryRun) {
  Write-Host "`nDryRun 完成：未构建、未写产物。" -ForegroundColor Green
  exit 0
}

if (-not $SkipBuild) {
  if ($WithRuntime -and -not $checks["resources staged"]) { throw "资源未 staged：先运行 stage-runtime.ps1" }

  Write-Step "构建（统一入口：先清理残留）"
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build.ps1 -Mode release
  if ($LASTEXITCODE -ne 0) { throw "构建失败（exit $LASTEXITCODE）" }
} else {
  Write-Step "跳过构建（-SkipBuild）：仅生成合规材料"
}

New-Item -ItemType Directory -Force -Path $out | Out-Null
$bundle = "src-tauri/target/release/bundle/nsis"
Get-ChildItem $bundle -Filter "*.exe" -ErrorAction SilentlyContinue | Copy-Item -Destination $out

Write-Step "生成校验和"
Get-ChildItem $out -File | Where-Object { $_.Name -ne "SHA256SUMS.txt" } | ForEach-Object {
  $hash = (Get-FileHash -Algorithm SHA256 -Path $_.FullName).Hash.ToLower()
  "$hash  $($_.Name)"
} | Set-Content -Path (Join-Path $out "SHA256SUMS.txt") -Encoding ASCII

Write-Step "生成 SBOM"
$pkg = Get-Content "package.json" -Raw | ConvertFrom-Json
$components = @()
foreach ($section in @("dependencies", "devDependencies")) {
  $deps = $pkg.$section
  if ($deps) {
    foreach ($name in $deps.PSObject.Properties.Name) {
      $components += [ordered]@{ type = "library"; name = $name; version = $deps.$name }
    }
  }
}
$cargo = Get-Content "src-tauri/Cargo.toml" -Raw
$cargoNames = [regex]::Matches($cargo, '(?m)^([a-zA-Z0-9_\-]+)\s*=\s*"') | ForEach-Object { $_.Groups[1].Value }
foreach ($name in $cargoNames) {
  $components += [ordered]@{ type = "library"; name = $name; version = "unspecified"; ecosystem = "cargo" }
}
$sbom = [ordered]@{
  bomFormat    = "CycloneDX"
  specVersion  = "1.5"
  version      = 1
  metadata     = [ordered]@{ component = [ordered]@{ type = "application"; name = "SVC Studio"; version = $Version } }
  components   = @([ordered]@{ type = "library"; name = "so-vits-svc"; version = "4.1-Stable"; licenses = @([ordered]@{ license = [ordered]@{ id = "AGPL-3.0-only" } }) }) + $components
}
$sbomText = $sbom | ConvertTo-Json -Depth 6
# 必须是 UTF-8 无 BOM：带 BOM 的 JSON 会让标准解析器直接报错（A3 要求可解析）。
[System.IO.File]::WriteAllText((Join-Path $out "SBOM.json"), $sbomText, (New-Object System.Text.UTF8Encoding($false)))

Write-Step "生成许可证目录"
$lic = Join-Path $out "licenses"
New-Item -ItemType Directory -Force -Path $lic | Out-Null
Copy-Item "vendor/so-vits-svc/LICENSE" (Join-Path $lic "AGPL-3.0.txt")
@"
SVC Studio $Version 源码获取方式
1. 随安装包附带的 source-$Version.zip 即为完整对应源码。
2. 亦可从项目仓库获取同版本 tag 的源码与构建脚本（build.ps1 / scripts/release.ps1）。
3. 本要约自发布日起 3 年内有效。
"@ | Set-Content -Path (Join-Path $lic "SOURCE-OFFER.txt") -Encoding UTF8

Write-Step "源码归档"
# 用 tar 直接从工作树打包并排除重型目录：不再先复制到 %TEMP%（C 盘常常没空间），
# 也避免把 src-tauri/target 这类构建产物塞进 AGPL 源码包。
$sourceZip = Join-Path $out "source-$Version.zip"
if (Test-Path $sourceZip) { Remove-Item -Force $sourceZip }
$tarArgs = @(
  "-a", "-c", "-f", $sourceZip,
  "--exclude=node_modules",
  "--exclude=src-tauri/target",
  "--exclude=dist",
  "--exclude=artifacts",
  "--exclude=.git",
  "--exclude=runtime",
  "--exclude=*.exe",
  "."
)
& tar.exe @tarArgs
if ($LASTEXITCODE -ne 0) { throw "源码归档失败（tar exit $LASTEXITCODE）" }
Write-Host ("源码包：" + [math]::Round((Get-Item $sourceZip).Length / 1MB, 1) + " MB")

Write-Step "合规自检"
& python docs/requests/intent_770b99fe990a4a0d/tools/check_compliance.py $out
if ($LASTEXITCODE -ne 0) { throw "合规材料自检失败：不应发布" }

Write-Host "`n发布产物已生成：$out" -ForegroundColor Green
